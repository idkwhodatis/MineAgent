"""Fake Docker state-machine tests. Real containment has a separate required gate."""

import json
import os
import subprocess
import time
from uuid import UUID

import pytest

from packages.factors.parser import parse_partition
from packages.factors.storage import write_partition
from runtimes.factor_parser.supervisor import (
    DockerParser,
    ParserIsolationError,
    assert_quiescent,
    factor_fence,
    prepare_operation,
)

from .test_storage import sample

IMAGE = "sha256:" + "a" * 64
IDENTITY = "b" * 64


class FakeDocker(DockerParser):
    def __init__(self, **kwargs):
        super().__init__(image_id=IMAGE, docker_binary="/fake/docker")
        self.container = None
        self.commands = []
        self.fail_create = False
        self.foreign = False
        self.unknown = False

    def _command(self, *args, timeout=2):
        self.commands.append(args)
        if self.unknown:
            raise ParserIsolationError("inspect_unknown")
        if args[0] == "inspect":
            if self.container is None:
                return subprocess.CompletedProcess(args, 1, b"", b"No such object: x")
            return subprocess.CompletedProcess(
                args, 0, json.dumps([self.container]).encode(), b""
            )
        if args[0] == "create":
            if self.fail_create:
                raise ParserIsolationError("create_uncertain")
            labels = {}
            for i, value in enumerate(args):
                if value == "--label":
                    k, v = args[i + 1].split("=", 1)
                    labels[k] = v
            if self.foreign:
                labels["io.mineagent.operation"] = str(UUID(int=99))
            self.container = {
                "Id": IDENTITY,
                "Image": IMAGE,
                "Config": {"Labels": labels},
                "State": {"Running": False, "ExitCode": 0, "OOMKilled": False},
            }
            return subprocess.CompletedProcess(args, 0, IDENTITY.encode(), b"")
        if args[:2] == ("rm", "--force"):
            assert args[2] == IDENTITY
            self.container = None
            return subprocess.CompletedProcess(args, 0, b"", b"")
        raise AssertionError(args)

    def _stream(self, identity, output, deadline):
        return parse_partition(output.parent / "input" / "input.parquet", output)


@pytest.fixture
def operation(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 65532)
    root = tmp_path / "root"
    op = root / "factors" / str(UUID(int=1)) / "staging" / str(UUID(int=2))
    partition = write_partition([sample()], tmp_path / "source.parquet")
    (root / "factors").mkdir(parents=True)
    with factor_fence(root) as fence:
        prepare_operation(
            root, op, image_id=IMAGE, input_sha256=partition.sha256, fence_fd=fence
        )
    for name in ("input", "output", "control"):
        (op / name).mkdir(parents=True)
    (op / "input" / "input.parquet").write_bytes(partition.path.read_bytes())
    return root, op, partition.sha256


def test_success_bounds_and_no_credential_inheritance(operation, monkeypatch):
    root, op, digest = operation
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    runner = FakeDocker()
    report = runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    assert report["input_sha256"] == digest
    assert set(runner.env) == {"PATH", "LANG"}
    create = next(c for c in runner.commands if c[0] == "create")
    assert (
        "--network=none" in create
        and "--read-only" in create
        and "--log-driver=none" in create
    )
    assert "--memory=512m" in create and "--pids-limit=32" in create
    assert sum(x == "--mount" for x in create) == 1
    assert "dst=/input,readonly" in create[create.index("--mount") + 1]
    assert not any(
        str(op / "control") in arg or str(op / "output") in arg for arg in create
    )
    assert_quiescent(root)
    assert runner.container is None


def test_unknown_create_absence_remains_fenced(operation):
    root, op, digest = operation
    runner = FakeDocker()
    runner.fail_create = True
    with pytest.raises(ParserIsolationError):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    assert not runner.reconcile_stop(root, op)
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)
    assert not (op / "control" / "stopped.json").exists()


def test_foreign_container_never_removed(operation):
    root, op, digest = operation
    runner = FakeDocker()
    runner.foreign = True
    with pytest.raises(ParserIsolationError):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    assert not any(c[0] == "rm" for c in runner.commands)
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)


def test_nonblocking_factor_fence(operation):
    root, _op, _ = operation
    with factor_fence(root), pytest.raises(ParserIsolationError), factor_fence(root):
        pass


def test_missing_control_blocks_new_writer(operation):
    root, _, _ = operation
    with pytest.raises((ParserIsolationError, FileNotFoundError)):
        assert_quiescent(root)


def test_stop_receipt_is_not_database_success(operation):
    root, op, digest = operation
    runner = FakeDocker()
    runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    stop = json.loads((op / "control" / "stopped.json").read_text())
    assert "published" not in stop and "resource_id" not in stop
    assert runner.reconcile_stop(root, op)


def test_operation_reuse_rejected(operation):
    root, op, digest = operation
    runner = FakeDocker()
    runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    with pytest.raises(ParserIsolationError):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)


def test_symlink_control_rejected(operation, tmp_path):
    root, op, _ = operation
    (op / "control").rmdir()
    (op / "control").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((OSError, ParserIsolationError)):
        assert_quiescent(root)


def test_crash_after_create_before_id_journal_reconciles_owned_token(operation):
    root, op, digest = operation
    runner = FakeDocker()

    # Abort after durable create identity but before normal attach, leaving live
    # runtime state; emulate supervisor death, not an API-managed cancellation.
    class Crash(BaseException):
        pass

    runner._stream = lambda *args: (_ for _ in ()).throw(Crash())
    original_stop = runner._stop
    runner._stop = lambda *args: False
    with pytest.raises(Crash):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    (op / "control" / "container.json").unlink()
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)
    runner._stop = original_stop
    assert runner.reconcile_stop(root, op)
    assert_quiescent(root)


def test_runtime_unreachable_keeps_barrier(operation):
    root, op, digest = operation
    runner = FakeDocker()
    runner.fail_create = True
    with pytest.raises(ParserIsolationError):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    runner.unknown = True
    assert not runner.reconcile_stop(root, op)
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)


def test_stopped_history_can_cold_restore_to_new_inode(operation, tmp_path):
    import shutil

    root, op, digest = operation
    runner = FakeDocker()
    runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)
    restored = tmp_path / "restored"
    shutil.copytree(root, restored)
    assert root.stat().st_ino != restored.stat().st_ino
    assert_quiescent(restored)
    restored_op = restored / op.relative_to(root)
    (restored_op / "control" / "stopped.json").unlink()
    with pytest.raises(ParserIsolationError):
        assert_quiescent(restored)
    with pytest.raises(ParserIsolationError, match="fence_changed"):
        runner.reconcile_stop(restored, restored_op)


def test_prelaunch_abort_retains_input_and_never_invents_container(operation):
    root, op, digest = operation
    runner = FakeDocker()
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)
    original = (op / "input" / "input.parquet").read_bytes()
    assert runner.reconcile_stop(root, op)
    assert_quiescent(root)
    receipt = json.loads((op.parent / f"aborted-{op.name}.json").read_text())
    assert receipt["never_launched"] is True and "container_id" not in receipt
    assert runner.commands == []
    assert (op / "input" / "input.parquet").read_bytes() == original
    with pytest.raises(ParserIsolationError):
        runner.run(root, op, input_sha256=digest, deadline=time.monotonic() + 20)


def test_crash_after_prepare_before_directory_recovers(tmp_path):
    root = tmp_path / "root"
    (root / "factors").mkdir(parents=True)
    op = root / "factors" / str(UUID(int=1)) / "staging" / str(UUID(int=2))
    with factor_fence(root) as fence:
        prepare_operation(
            root, op, image_id=IMAGE, input_sha256="a" * 64, fence_fd=fence
        )
    assert not op.exists()
    with pytest.raises(ParserIsolationError):
        assert_quiescent(root)
    assert FakeDocker().reconcile_stop(root, op)
    assert_quiescent(root)


def test_truncated_launch_is_unknown_not_never_launched(operation):
    root, op, _ = operation
    (op / "control" / "launch.json").write_bytes(b"{")
    with pytest.raises(ParserIsolationError):
        FakeDocker().reconcile_stop(root, op)
    assert not (op.parent / f"aborted-{op.name}.json").exists()


def test_inherited_publication_fence_does_not_relock(operation):
    root, op, digest = operation
    with factor_fence(root) as fence:
        report = FakeDocker().run(
            root,
            op,
            input_sha256=digest,
            deadline=time.monotonic() + 20,
            fence_fd=fence,
        )
        assert report["input_sha256"] == digest
        with pytest.raises(ParserIsolationError), factor_fence(root):
            pass
    assert_quiescent(root)


def test_archived_prelaunch_abort_survives_cold_copy(operation, tmp_path):
    import shutil

    root, op, _ = operation
    assert FakeDocker().reconcile_stop(root, op)
    restored = tmp_path / "aborted-restored"
    shutil.copytree(root, restored)
    assert_quiescent(restored)


def test_stream_diagnostics_never_forward_hostile_text():
    from runtimes.factor_parser.supervisor import _stream_failure

    assert (
        _stream_failure([ValueError("password=hostile-secret")]).code == "parser_failed"
    )
    assert (
        _stream_failure([ParserIsolationError("hostile-secret")]).code
        == "parser_failed"
    )
    assert (
        _stream_failure([ParserIsolationError("parser_runtime_setup_failed")]).code
        == "parser_runtime_setup_failed"
    )
