"""Local authority/transport/filesystem checks; no Docker acceptance."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.openevolve_evaluator.journal import NativeInputBinding, NativeIntent
from runtimes.openevolve_evaluator.protected_output import frame
from runtimes.openevolve_evaluator.transport import read_native_frame
from runtimes.research_supervisor.openevolve_native_supervisor import (
    NativeAuthority,
    NativeSupervisor,
    _mint_native_authority,
    check_input_bytes,
    read_manifest,
    staged_name,
)
from tests.openevolve.test_native_evaluator_contracts import manifest
from tests.openevolve.test_native_evaluator_journal import records


def bound():
    launch, *_ = records()
    value, files = manifest()
    raw = canonical(value.model_dump(mode="json"))
    binding = NativeInputBinding(
        evaluation_id=value.evaluation_id,
        manifest_object_id=launch.intent.inputs.manifest_object_id,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        manifest_size_bytes=len(raw),
        native_artifacts_sha256=value.native_artifacts_sha256,
        execution_plan_sha256=value.execution_plan_sha256,
        source_sha256=value.source_sha256,
    )
    admission = launch.intent.admission.model_copy(
        update={
            "logical_operation_id": value.evaluation_id,
            "input_sha256": digest(binding.model_dump(mode="json")),
        }
    )
    now = datetime.now(UTC)
    intent = NativeIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    return intent, value, {"/input/" + p: b for p, b in files.items()}


def test_fixed_bytes_and_single_consumption():
    intent, value, files = bound()
    check_input_bytes(intent, value, files)
    with pytest.raises(ProcessError):
        NativeAuthority(object(), intent, value, files, {})
    authority = _mint_native_authority(intent, value, files, {})
    assert authority.consume() == {}
    with pytest.raises(ProcessError):
        authority.consume()
    with pytest.raises(ValueError):
        check_input_bytes(intent, value, {**files, "/input/strategy.py": b"bad"})


def test_unbound_files_or_manifest_cannot_be_swapped():
    intent, value, files = bound()
    with pytest.raises(ProcessError):
        check_input_bytes(intent, value, {**files, "/input/unlisted": b"bad"})
    other, _ = manifest()
    with pytest.raises(ProcessError):
        check_input_bytes(intent, other, files)


def test_staging_names_preserve_distinct_nested_destinations():
    assert staged_name("/input/data/A.csv") != staged_name("/input/universe/A.csv")
    assert "/" not in staged_name("/input/data/A.csv")


def test_retained_manifest_tampering_and_symlinks_rejected(tmp_path):
    intent, value, _ = bound()
    launch = records()[0].model_copy(
        update={
            "intent": intent,
            "runtime_name": "mineagent-oe-native-" + str(intent.id),
        }
    )
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical(value.model_dump(mode="json")))
    path.chmod(0o600)
    assert read_manifest(tmp_path, launch) == value
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        read_manifest(tmp_path, launch)
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        read_manifest(tmp_path, launch)


def test_create_arguments_have_exact_new_profile_limits():
    intent, _, files = bound()
    launch = records()[0].model_copy(
        update={
            "intent": intent,
            "runtime_name": "mineagent-oe-native-" + str(intent.id),
        }
    )
    supervisor = NativeSupervisor(
        intent.admission.image_id, installed_parent_image_id="sha256:" + "f" * 64
    )
    args = supervisor._create_args(
        launch, {p: "/retained/" + staged_name(p) for p in files}
    )
    assert args[args.index("--memory") + 1] == "1g"
    assert args[args.index("--pids-limit") + 1] == "64"
    assert args[args.index("--ipc") + 1] == "none"
    assert len([a for a in args if a == "--mount"]) == len(files)
    assert "io.mineagent.oe-native.admission=" + str(intent.id) in args


class Reader:
    def __init__(self, frames):
        self.frames = iter(frames)

    def read_frame(self, **_):
        return next(self.frames, None)


def test_frame_fragmentation_does_not_accept_trailing_bytes():
    raw = frame(2, b"payload")
    assert read_native_frame(Reader([(1, raw[:4]), (1, raw[4:])]), 0) == (2, b"payload")
    with pytest.raises(ValueError):
        read_native_frame(Reader([(1, raw + b"x")]), 0)
    with pytest.raises(ValueError):
        read_native_frame(Reader([(2, raw)]), 0)


def persisted_launch(root):
    import os

    from runtimes.research_supervisor.supervisor import process_fence, write_record

    intent, value, _ = bound()
    with process_fence(root) as namespace:
        owner = namespace / str(intent.admission.owner_id)
        owner.mkdir(mode=0o700)
        operation = owner / str(intent.id)
        operation.mkdir(mode=0o700)
        (operation / "control").mkdir(mode=0o700)
        info = os.stat(root)
        launch = records()[0].model_copy(
            update={
                "intent": intent,
                "runtime_name": "mineagent-oe-native-" + str(intent.id),
                "root_device": info.st_dev,
                "root_inode": info.st_ino,
            }
        )
        write_record(
            operation / "control" / "launch.json", launch.model_dump(mode="json")
        )
        path = operation / "manifest.json"
        path.write_bytes(canonical(value.model_dump(mode="json")))
        path.chmod(0o600)
    return intent.admission, operation


@pytest.mark.parametrize("status", ["created", "exited", "running_observation_lost"])
def test_exact_unobserved_container_cleanup_never_invents_stop(
    monkeypatch, tmp_path, status
):
    admission, path = persisted_launch(tmp_path)
    state = {
        "Id": "c" * 64,
        "State": {
            "Running": status == "running_observation_lost",
            "Status": status,
            "StartedAt": "0001-01-01T00:00:00Z"
            if status == "created"
            else "2026-01-01T00:00:00Z",
        },
    }
    commands, verified = [], []

    class Client:
        deadline = None
        present = True

        def _inspect(self, _name):
            return state if self.present else None

        def _command(self, args):
            commands.append(args)
            assert args == ["rm", "--force", state["Id"]]
            self.present = False
            return 0, b""

    supervisor = NativeSupervisor(admission.image_id, "sha256:" + "f" * 64)
    supervisor.client = Client()
    monkeypatch.setattr(supervisor, "verified_inventory", lambda _role: {})
    monkeypatch.setattr(
        supervisor, "_verify", lambda *args: verified.append(args) or state
    )

    def missing_identity(*_args):
        raise ProcessError("oe_native_process_kernel_identity")

    monkeypatch.setattr(supervisor, "_observe_identity", missing_identity)
    with pytest.raises(
        ProcessError,
        match="created_cleanup_uncertain"
        if status == "created"
        else "identity_unobserved",
    ):
        supervisor.stop_and_verify(tmp_path, admission)
    assert len(verified) == 1 and len(commands) == 1
    assert not (path / "control" / "identity.json").exists()
    assert not (path / "control" / "stop.json").exists()
    # A second read after container deletion still cannot invent stop evidence.
    with pytest.raises(ProcessError, match="identity_unobserved"):
        supervisor.stop_and_verify(tmp_path, admission)
    assert len(commands) == 1


def test_unverified_container_is_not_deleted(monkeypatch, tmp_path):
    admission, _ = persisted_launch(tmp_path)
    supervisor = NativeSupervisor(admission.image_id, "sha256:" + "f" * 64)
    commands = []
    supervisor.client = SimpleNamespace(
        deadline=None,
        _inspect=lambda _n: {"Id": "c" * 64},
        _command=lambda args: commands.append(args),
    )
    monkeypatch.setattr(supervisor, "verified_inventory", lambda _role: {})

    def reject(*_args):
        raise ProcessError("oe_native_process_runtime_identity")

    monkeypatch.setattr(supervisor, "_verify", reject)
    with pytest.raises(ProcessError, match="runtime_identity"):
        supervisor.stop_and_verify(tmp_path, admission)
    assert commands == []


def test_manifest_reader_does_not_follow_operation_directory_symlink(tmp_path):
    root = tmp_path / "real"
    root.mkdir(mode=0o700)
    admission, path = persisted_launch(root)
    supervisor = NativeSupervisor(admission.image_id, "sha256:" + "f" * 64)
    launch = supervisor._launch(path)
    linked = tmp_path / "link"
    linked.symlink_to(path, target_is_directory=True)
    with pytest.raises(OSError):
        read_manifest(linked, launch)
