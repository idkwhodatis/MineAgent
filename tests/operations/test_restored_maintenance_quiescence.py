"""Actual restore-copy and stopped typed journals, not full DB settlement proof."""

import hashlib
import json
import os
from uuid import uuid4

import pytest

from backend.app.core.storage_barrier import storage_guard
from backend.app.maintenance_process_quiescence import assert_maintenance_quiescent
from backend.app.operations_backup import _copy, _shared_storage_reader
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.supervisor import assert_quiescent
from tests.research_runtime.test_modern_cold_quiescence import FACTORIES, persist


def restored(tmp_path, kind):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    if kind == "legacy":
        from tests.research_runtime.test_process_registry import stopped_journal

        _, operation = stopped_journal(source)
    elif kind == "numeric":
        from tests.research_runtime.test_numeric_process_boundary import journal

        _, launch, *_ = journal(source, "factor_cutoff_candidate")
        admission = launch.intent.admission
        operation = (
            source / "research-processes" / str(admission.owner_id) / str(admission.id)
        )
    else:
        operation = persist(source, kind)
    for p in source.rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            key = p.relative_to(source).as_posix()
            _copy(
                source,
                target / key,
                {
                    "path": key,
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                target,
            )

    return source, target, target / operation.relative_to(source)


def check(root):
    with storage_guard(root, exclusive=True) as fd:
        assert_maintenance_quiescent(root, root_fd=fd)


@pytest.mark.parametrize("kind", [*FACTORIES, "numeric", "legacy"])
@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_restore_copy_and_original_modes_have_identical_stopped_evidence(
    tmp_path, kind, mode
):
    source, target, operation = restored(tmp_path, kind)
    files = [p for p in target.rglob("*") if p.is_file()]
    assert files and all(p.stat().st_mode & 0o777 == 0o400 for p in files)
    check(source)
    if mode == 0o600:
        for p in files:
            p.chmod(mode)
    check(target)
    if mode == 0o400 and kind != "legacy":
        with pytest.raises(ProcessError):
            assert_quiescent(target)
    assert operation.is_dir()


@pytest.mark.parametrize("kind", FACTORIES)
@pytest.mark.parametrize(
    "fault",
    [
        "missing_stop",
        "live",
        "hash",
        "profile",
        "owner",
        "unit",
        "extra_control",
        "extra_directory",
        "symlink",
        "hardlink",
    ],
)
def test_restored_stopped_chain_remains_exact(tmp_path, kind, fault):
    _, root, operation = restored(tmp_path, kind)
    control = operation / "control"
    p = control / "stop.json"
    if fault == "missing_stop":
        p.unlink()
    elif fault in {"live", "hash", "profile"}:
        if fault == "profile":
            p = control / "launch.json"
        x = json.loads(p.read_bytes())
        if fault == "live":
            x["confirmed_absent"] = False
        elif fault == "hash":
            x["launch_sha256"] = "0" * 64
        else:
            x["intent"]["admission"]["profile"] = "unknown"
        p.chmod(0o600)
        p.write_bytes(canonical(x))
        p.chmod(0o400)
    elif fault == "owner":
        operation.parent.rename(operation.parent.with_name(str(uuid4())))
    elif fault == "unit":
        operation.rename(operation.with_name(str(uuid4())))
    elif fault == "extra_control":
        (control / "unknown.json").write_bytes(b"{}")
    elif fault == "extra_directory":
        (operation / "input").mkdir()
    elif fault == "hardlink":
        os.link(p, root / "link")
    else:
        q = root / "outside"
        p.rename(q)
        p.symlink_to(q)
    with pytest.raises(ProcessError):
        check(root)


@pytest.mark.parametrize("mode", [0o444, 0o640, 0o644, 0o200, 0o000, 0o700])
def test_extra_permissions_or_missing_read_bit_rejected(tmp_path, mode):
    _, root, op = restored(tmp_path, "rd")
    (op / "control" / "launch.json").chmod(mode)
    with pytest.raises(ProcessError):
        check(root)


def test_nonowner_rejected(tmp_path, monkeypatch):
    _, root, _ = restored(tmp_path, "rd")
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(ProcessError):
        check(root)


@pytest.mark.parametrize("fault", ["hash", "missing", "symlink", "oversize"])
def test_native_manifest_remains_bound(tmp_path, fault):
    _, root, op = restored(tmp_path, "native")
    p = op / "manifest.json"
    if fault == "missing":
        p.unlink()
    elif fault == "symlink":
        q = root / "other"
        p.rename(q)
        p.symlink_to(q)
    else:
        p.chmod(0o600)
        p.write_bytes(b"x" * 65537 if fault == "oversize" else b"{}")
        p.chmod(0o400)
    with pytest.raises(ProcessError):
        check(root)


def test_wrong_or_closed_root_descriptor_rejected(tmp_path):
    _, root, _ = restored(tmp_path, "rd")
    other = tmp_path / "other"
    other.mkdir()
    with storage_guard(other, exclusive=True) as wrong, pytest.raises(ProcessError):
        assert_maintenance_quiescent(root, root_fd=wrong)
    with pytest.raises(ProcessError):
        assert_maintenance_quiescent(root, root_fd=wrong)


def test_backup_wrapper_still_calls_original_body_after_physical_quiescence(tmp_path):
    _, root, _ = restored(tmp_path, "rd")
    seen = []

    @_shared_storage_reader
    def body(**kwargs):
        seen.append(True)
        raise ValueError("subsequent full database closure refusal")

    with pytest.raises(ValueError, match="full database closure refusal"):
        body(storage_root=root, quiesced=True)
    assert seen == [True]
    with pytest.raises(ValueError):
        body(storage_root=root, quiesced=False)
    assert seen == [True]


@pytest.mark.parametrize("kind", [*FACTORIES, "numeric", "legacy"])
def test_unknown_launch_schema_cannot_be_reinterpreted(tmp_path, kind):
    _, root, operation = restored(tmp_path, kind)
    p = operation / "control" / "launch.json"
    value = json.loads(p.read_bytes())
    value["schema_version"] = "unknown.future.launch"
    p.chmod(0o600)
    p.write_bytes(canonical(value))
    p.chmod(0o400)
    with pytest.raises(ProcessError):
        check(root)


@pytest.mark.parametrize("component", ["operation", "control", "owner"])
def test_directory_symlink_is_not_followed(tmp_path, component):
    _, root, op = restored(tmp_path, "rd")
    path = (
        op
        if component == "operation"
        else op / "control"
        if component == "control"
        else op.parent
    )
    moved = root / "moved"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ProcessError):
        check(root)


@pytest.mark.parametrize("fault", ["mode", "replace", "hardlink"])
def test_control_changed_during_read_is_rejected(tmp_path, monkeypatch, fault):
    _, root, op = restored(tmp_path, "rd")
    path = op / "control" / "launch.json"
    original = os.read
    changed = False

    def race(fd, size):
        nonlocal changed
        raw = original(fd, size)
        if raw and not changed:
            changed = True
            if fault == "mode":
                path.chmod(0o600)
            elif fault == "hardlink":
                os.link(path, root / "link")
            else:
                data = path.read_bytes()
                path.rename(root / "old-launch")
                path.write_bytes(data)
                path.chmod(0o400)
        return raw

    monkeypatch.setattr(os, "read", race)
    with pytest.raises(ProcessError):
        check(root)
    assert changed


def test_malformed_legacy_admission_has_fixed_error_surface(tmp_path):
    _, root, op = restored(tmp_path, "legacy")
    p = op / "control" / "launch.json"
    value = json.loads(p.read_bytes())
    del value["admission"]["id"]
    p.chmod(0o600)
    p.write_bytes(canonical(value))
    p.chmod(0o400)
    with pytest.raises(ProcessError, match="^maintenance_process_integrity$"):
        check(root)
