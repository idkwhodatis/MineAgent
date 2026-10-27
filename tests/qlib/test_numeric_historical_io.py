"""Relocated actual file reads with synthetic numeric receipts, not PG restore."""

import shutil

import pytest

from backend.app.factor_native_io import native_storage_guard
from backend.app.numeric_historical_io import read_historical_numeric_bytes
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_journal import read_numeric_journal
from runtimes.research_supervisor.numeric_supervisor import NumericSupervisor
from tests.research_runtime.test_numeric_process_boundary import journal


@pytest.mark.parametrize("role", ["factor_cutoff_candidate", "factor_normalizer"])
def test_relocated_numeric_history_is_not_live_or_launch_authority(tmp_path, role):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    admission, launch, identity, stop, extraction, raw, _ = journal(original, role)
    (original / "factors").mkdir(mode=0o700)
    restored = tmp_path / "restored"
    shutil.copytree(original, restored)
    with pytest.raises(ProcessError, match="root_identity"):
        read_numeric_journal(restored, admission, require_output=True)
    with native_storage_guard(restored):
        result = read_historical_numeric_bytes(
            restored,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction,
        )
    assert result.output_bytes == raw and result.report_bytes is None
    with pytest.raises(ProcessError, match="authority"):
        NumericSupervisor(admission.image_id).execute(
            restored, result, on_identity=lambda _: None
        )


def test_historical_report_bytes_must_still_match_protected_receipt(tmp_path):
    import hashlib

    from packages.research_runtime.numeric_process_journal import (
        NumericReportExtraction,
    )
    from runtimes.research_supervisor.supervisor import write_record

    _, launch, identity, stop, extraction, _, path = journal(
        tmp_path, "factor_normalizer"
    )
    report = b'{"synthetic_structure_only":true}'
    payload = extraction.model_dump(mode="json")
    payload.update(
        schema_version="mineagent.numeric-process-extraction.v3",
        protocol="MANU2-report-v1",
        report_basename="rows.json",
        report_sha256=hashlib.sha256(report).hexdigest(),
        report_size_bytes=len(report),
        input_manifest_sha256="a" * 64,
        listings_sha256="a" * 64,
    )
    extraction = NumericReportExtraction.model_validate(payload)
    (path / "control" / "extraction.json").unlink()
    write_record(
        path / "control" / "extraction.json", extraction.model_dump(mode="json")
    )
    retained = path / "output" / "rows.json"
    retained.write_bytes(report)
    retained.chmod(0o600)
    (tmp_path / "factors").mkdir(mode=0o700)
    with native_storage_guard(tmp_path):
        result = read_historical_numeric_bytes(
            tmp_path,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction,
        )
        assert result.report_bytes == report
        retained.write_bytes(b"x" * len(report))
        with pytest.raises(ProcessError):
            read_historical_numeric_bytes(
                tmp_path,
                intent=launch.intent,
                identity=identity,
                stop=stop,
                extraction=extraction,
            )
