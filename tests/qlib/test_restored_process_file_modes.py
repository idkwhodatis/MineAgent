"""Real restore-copy/file guards with synthetic typed receipts, not PG execution."""

import hashlib
import os

import pytest

from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.historical_process_files import read_historical_record
from backend.app.operations_backup import _copy
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_journal import read_numeric_record


def restored_fixture(tmp_path, family):
    original, restored = tmp_path / "original", tmp_path / "restored"
    original.mkdir(mode=0o700)
    restored.mkdir(mode=0o700)
    (original / "factors").mkdir(mode=0o700)
    (restored / "factors").mkdir(mode=0o700)
    if family == "numeric":
        from backend.app.numeric_historical_io import (
            read_historical_numeric_bytes as read,
        )
        from runtimes.research_supervisor.numeric_journal import (
            read_opaque_output as live,
        )
        from tests.research_runtime.test_numeric_process_boundary import journal

        _, launch, identity, stop, extraction, raw, _ = journal(
            original, "factor_cutoff_candidate"
        )
    elif family == "analysis":
        from backend.app.analysis_historical_io import (
            read_historical_analysis_bytes as read,
        )
        from runtimes.research_supervisor.analysis_journal import (
            read_opaque_output as live,
        )
        from tests.qlib.test_analysis_files import fixture

        launch, identity, stop, extraction, raw, _ = fixture(original)
    else:
        from backend.app.structured_historical_io import (
            read_historical_structured_bytes as read,
        )
        from runtimes.research_supervisor.structured_factor_journal import (
            read_opaque_output as live,
        )
        from tests.structured_factor_strategy.test_files import fixture

        launch, identity, stop, extraction, raw, _ = fixture(original)
    for path in original.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            key = path.relative_to(original).as_posix()
            _copy(
                original,
                restored / key,
                {
                    "path": key,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
                restored,
            )
    return restored, read, live, launch, identity, stop, extraction, raw


@pytest.mark.parametrize("family", ["numeric", "analysis", "structured"])
@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_real_restore_copy_modes_allow_history_but_not_live_authority(
    tmp_path, family, mode
):
    root, read, live, launch, identity, stop, extraction, raw = restored_fixture(
        tmp_path, family
    )
    files = [p for p in root.rglob("*") if p.is_file()]
    assert files and all(p.stat().st_mode & 0o777 == 0o400 for p in files)
    if mode == 0o600:
        for path in files:
            path.chmod(mode)
    kwargs = {
        "intent": launch.intent,
        "identity": identity,
        "stop": stop,
        "extraction": extraction,
    }
    with pytest.raises(NativeStorageError):
        read(root, **kwargs)
    if mode == 0o400:
        for path in files:
            if path.name == "launch.json":
                with pytest.raises(ProcessError):
                    read_numeric_record(path)
        with pytest.raises(ProcessError):
            live(root, launch.intent.admission, extraction)
    with native_storage_guard(root):
        result = read(root, **kwargs)
    assert getattr(result, "output_bytes", getattr(result, "result_bytes", None)) == raw
    assert result.qualification == "historical_integrity_not_live_authority"


def record(tmp_path):
    (tmp_path / "factors").mkdir(mode=0o700)
    path = tmp_path / "control.json"
    path.write_bytes(b'{"ok":true}')
    path.chmod(0o400)
    return path


@pytest.mark.parametrize(
    "fault",
    [
        "0444",
        "0640",
        "0644",
        "0200",
        "0000",
        "0700",
        "hardlink",
        "symlink",
        "parent_link",
        "owner",
        "empty",
        "oversize",
        "duplicate",
        "noncanonical",
    ],
)
def test_historical_control_metadata_stays_fail_closed(tmp_path, monkeypatch, fault):
    path = record(tmp_path)
    if fault[0] == "0":
        path.chmod(int(fault, 8))
    elif fault == "hardlink":
        os.link(path, tmp_path / "other")
    elif fault == "symlink":
        target = tmp_path / "other"
        path.rename(target)
        path.symlink_to(target)
    elif fault == "parent_link":
        folder = tmp_path / "real"
        folder.mkdir()
        path.rename(folder / path.name)
        (tmp_path / "linked").symlink_to(folder, target_is_directory=True)
        path = tmp_path / "linked" / path.name
    elif fault == "owner":
        monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    else:
        path.chmod(0o600)
        path.write_bytes(
            {
                "empty": b"",
                "oversize": b"x" * 16385,
                "duplicate": b'{"ok":true,"ok":true}',
                "noncanonical": b'{ "ok":true}',
            }[fault]
        )
        path.chmod(0o400)
    with native_storage_guard(tmp_path), pytest.raises(ProcessError):
        read_historical_record(tmp_path, path)


@pytest.mark.parametrize(
    "fault", ["mode", "truncate", "replace", "hardlink", "same_length_write"]
)
def test_read_time_mutation_rejected_by_full_postread_identity(
    tmp_path, monkeypatch, fault
):
    path = record(tmp_path)
    # Rewriting identical bytes and restoring the mode can occur within one
    # filesystem timestamp tick. Give the write an observable mtime change
    # instead of relying on wall-clock progress to exercise the identity guard.
    if fault == "same_length_write":
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = path.stat()
    original = os.read
    changed = False

    def read(fd, count):
        nonlocal changed
        raw = original(fd, count)
        if not changed:
            changed = True
            if fault == "mode":
                path.chmod(0o600)
            elif fault == "truncate":
                path.chmod(0o600)
                path.write_bytes(b"{}")
                path.chmod(0o400)
            elif fault == "replace":
                new = tmp_path / "new"
                new.write_bytes(path.read_bytes())
                new.chmod(0o400)
                new.replace(path)
            elif fault == "hardlink":
                os.link(path, tmp_path / "other")
            else:
                path.chmod(0o600)
                path.write_bytes(b'{"ok":true}')
                path.chmod(0o400)
                assert path.stat().st_mtime_ns != before.st_mtime_ns
        return raw

    monkeypatch.setattr(os, "read", read)
    with native_storage_guard(tmp_path), pytest.raises(ProcessError):
        read_historical_record(tmp_path, path)


def test_structured_history_preserves_closed_output_inventory(tmp_path):
    root, read, _, launch, identity, stop, extraction, _ = restored_fixture(
        tmp_path, "structured"
    )
    from runtimes.research_supervisor.supervisor import operation_path

    (
        operation_path(root, launch.intent.admission) / "output" / "extra.json"
    ).write_bytes(b"{}")
    with native_storage_guard(root), pytest.raises(ProcessError):
        read(
            root,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction,
        )


@pytest.mark.parametrize("family", ["numeric", "analysis", "structured"])
@pytest.mark.parametrize("fault", ["truncate", "hash", "symlink", "hardlink", "0644"])
def test_historical_output_still_requires_exact_original_file(tmp_path, family, fault):
    root, read, _, launch, identity, stop, extraction, raw = restored_fixture(
        tmp_path, family
    )
    from runtimes.research_supervisor.supervisor import operation_path

    path = (
        operation_path(root, launch.intent.admission) / "output" / extraction.basename
    )
    if fault in {"truncate", "hash"}:
        path.chmod(0o600)
        path.write_bytes(raw[:-1] if fault == "truncate" else b"x" * len(raw))
        path.chmod(0o400)
    elif fault == "0644":
        path.chmod(0o644)
    elif fault == "hardlink":
        os.link(path, root / "extra")
    else:
        target = root / "other"
        path.rename(target)
        path.symlink_to(target)
    with native_storage_guard(root), pytest.raises(ProcessError):
        read(
            root,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction,
        )


def test_numeric_report_readonly_bytes_and_hash_remain_required(tmp_path):
    from backend.app.numeric_historical_io import read_historical_numeric_bytes
    from packages.research_runtime.numeric_process_journal import (
        NumericReportExtraction,
    )
    from runtimes.research_supervisor.supervisor import write_record
    from tests.research_runtime.test_numeric_process_boundary import journal

    _, launch, identity, stop, extraction, _, path = journal(
        tmp_path, "factor_normalizer"
    )
    report = b'{"synthetic_structure_only":true}'
    extraction = NumericReportExtraction.model_validate(
        {
            **extraction.model_dump(mode="json"),
            "schema_version": "mineagent.numeric-process-extraction.v3",
            "protocol": "MANU2-report-v1",
            "report_basename": "rows.json",
            "report_sha256": hashlib.sha256(report).hexdigest(),
            "report_size_bytes": len(report),
            "input_manifest_sha256": "a" * 64,
            "listings_sha256": "a" * 64,
        }
    )
    (path / "control" / "extraction.json").unlink()
    write_record(
        path / "control" / "extraction.json", extraction.model_dump(mode="json")
    )
    retained = path / "output" / "rows.json"
    retained.write_bytes(report)
    for item in path.rglob("*"):
        if item.is_file():
            item.chmod(0o400)
    (tmp_path / "factors").mkdir(mode=0o700)
    kwargs = {
        "intent": launch.intent,
        "identity": identity,
        "stop": stop,
        "extraction": extraction,
    }
    with native_storage_guard(tmp_path):
        assert read_historical_numeric_bytes(tmp_path, **kwargs).report_bytes == report
        retained.chmod(0o600)
        retained.write_bytes(b"x" * len(report))
        retained.chmod(0o400)
        with pytest.raises(ProcessError):
            read_historical_numeric_bytes(tmp_path, **kwargs)
