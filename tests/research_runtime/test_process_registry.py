"""Pure contracts/journal tests, not a replacement for real Docker acceptance."""

import hashlib
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import (
    ProcessAdmission,
    ProcessError,
    digest,
)
from runtimes.research_supervisor.supervisor import (
    IDENTITY_SCHEMA,
    STOP_SCHEMA,
    _launch_value,
    assert_quiescent,
    load_launch,
    read_record,
    validate_stopped_journal,
    write_record,
)


def admission():
    return ProcessAdmission(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        1,
        "neutral_fixture",
        uuid4(),
        1,
        "neutral_fixture.v1",
        "a" * 64,
        uuid4(),
        "sha256:" + "b" * 64,
        hashlib.sha256(("c" * 64).encode()).hexdigest(),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"role": "rdagent"},
        {"profile": "openevolve"},
        {"generation": True},
        {"launch_ordinal": 0},
        {"image_id": "python:latest"},
        {"ticket_sha256": "secret"},
    ],
)
def test_process_admission_is_closed(change):
    with pytest.raises(ProcessError):
        replace(admission(), **change)


def stopped_journal(tmp_path):
    a = admission()
    path = tmp_path / "research-processes" / str(a.owner_id) / str(a.id)
    (path / "control").mkdir(parents=True)
    launch = _launch_value(
        tmp_path,
        a,
        {
            "schema_version": "mineagent.research-process-intent.v1",
            "intent_id": str(uuid4()),
            "admission_sha256": digest(a.public()),
            "issued_at": "2026-01-01T00:00:00+00:00",
            "not_after": "2026-01-01T00:01:00+00:00",
        },
    )
    write_record(path / "control/launch.json", launch)
    identity = {
        "schema_version": IDENTITY_SCHEMA,
        "launch_sha256": digest(launch),
        "admission_sha256": digest(a.public()),
        "container_id": "d" * 64,
        "observed_state": "created",
        "host_pid": 0,
        "start_ticks": 0,
        "pid_namespace": "",
        "cgroup": "",
    }
    write_record(path / "control/identity.json", identity)
    stop = {
        "schema_version": STOP_SCHEMA,
        "launch_sha256": digest(launch),
        "container_id": "d" * 64,
        "confirmed_absent": True,
    }
    write_record(path / "control/stop.json", stop)
    return a, path


def test_offline_journal_validation_preserves_historical_inode(tmp_path):
    a, path = stopped_journal(tmp_path)
    assert validate_stopped_journal(path, a)["confirmed_absent"]
    assert_quiescent(tmp_path)
    # Offline validation never requires old root inode equal a restored disk.
    assert load_launch(path, a)["admission"] == a.public()


@pytest.mark.parametrize(
    "damage", ["missing-stop", "unknown-file", "symlink", "hardlink", "permissions"]
)
def test_unknown_or_tampered_journal_never_quiesces(tmp_path, damage):
    _a, path = stopped_journal(tmp_path)
    target = path / "control/stop.json"
    if damage == "missing-stop":
        target.unlink()
    elif damage == "unknown-file":
        (path / "unexpected").write_text("unaccounted")
    elif damage == "symlink":
        target.rename(path / "outside")
        target.symlink_to(path / "outside")
    elif damage == "hardlink":
        os.link(target, path / "outside")
    else:
        target.chmod(0o644)
    with pytest.raises((ProcessError, OSError)):
        assert_quiescent(tmp_path)


def test_duplicate_keys_and_mutable_journal_rejected(tmp_path):
    path = tmp_path / "record.json"
    path.write_text('{"a":1,"a":2}')
    path.chmod(0o600)
    with pytest.raises(ProcessError):
        read_record(path)
    path.unlink()
    write_record(path, {"a": 1})
    with pytest.raises(FileExistsError):
        write_record(path, {"a": 2})


def test_missing_namespace_compatible_but_symlink_not_compatible(tmp_path):
    assert_quiescent(tmp_path)
    (tmp_path / "research-processes").symlink_to(tmp_path)
    with pytest.raises(OSError):
        assert_quiescent(tmp_path)


def test_uncommitted_dataclass_cannot_authorize_runtime(tmp_path):
    from runtimes.research_supervisor.supervisor import (
        DockerSupervisor,
        LaunchAuthority,
    )

    a = admission()
    with pytest.raises(ProcessError, match="launch_authority"):
        DockerSupervisor(a.image_id).launch(tmp_path, a, "c" * 64)
    with pytest.raises(ProcessError, match="launch_authority"):
        LaunchAuthority(a, {})
    assert not (tmp_path / "research-processes").exists()


def test_pure_launch_capability_cannot_rebind_or_be_reused(tmp_path):
    from datetime import UTC, datetime, timedelta

    from runtimes.research_supervisor.supervisor import _mint_launch_authority

    a = admission()
    now = datetime.now(UTC)
    intent = {
        "schema_version": "mineagent.research-process-intent.v1",
        "intent_id": str(uuid4()),
        "admission_sha256": digest(a.public()),
        "issued_at": now.isoformat(),
        "not_after": (now + timedelta(seconds=60)).isoformat(),
    }
    # Pure contract inspection only: this test never invokes a runtime.
    authority = _mint_launch_authority(a, intent)
    with pytest.raises(AttributeError):
        authority.admission = admission()
    with pytest.raises(TypeError):
        authority.intent["admission_sha256"] = "f" * 64
    import json

    from packages.research_runtime.process_contracts import canonical

    assert (
        json.loads(canonical(_launch_value(tmp_path, a, authority.intent)))["intent"]
        == intent
    )
    assert authority.consume() == a
    with pytest.raises(ProcessError):
        authority.consume()


def test_cli_projection_is_bounded_while_subprocess_writes(tmp_path, monkeypatch):
    """Pure transport-bound fault injection, not real Docker containment evidence."""
    import sys

    from runtimes.research_supervisor.supervisor import DockerSupervisor

    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\nimport os\nwhile True: os.write(1, b'x' * 4096)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ProcessError, match="research_process_output"):
        DockerSupervisor("sha256:" + "b" * 64)._command(
            ["container", "inspect", "bounded"]
        )
