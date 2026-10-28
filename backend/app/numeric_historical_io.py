"""Historical stopped numeric bytes; never a live/native parser or launch grant.

Caller resolves current-owner terminal immutable DB closure and exact receipts.
Original execution root inode is preserved as history. This narrow reader only
validates current protected files against that chain under retained storage guard.
"""

from dataclasses import dataclass
from uuid import UUID

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.historical_process_files import (
    read_historical_output,
    read_historical_record,
)
from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericIntent,
    NumericLaunch,
    NumericReportExtraction,
    NumericStop,
    parse_numeric_extraction,
    validate_numeric_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.supervisor import operation_path


@dataclass(frozen=True)
class HistoricalNumericBytes:
    admission_id: UUID
    basename: str
    sha256: str
    output_bytes: bytes
    report_sha256: str | None
    report_bytes: bytes | None
    qualification: str = "historical_integrity_not_live_authority"


def _decode(cls, value):
    if isinstance(value, cls):
        value = value.model_dump(mode="json")
    return cls.model_validate_json(canonical(value))


def read_historical_numeric_bytes(root, *, intent, identity, stop, extraction):
    require_native_storage_guard(root)
    intent = _decode(NumericIntent, intent)
    identity = _decode(NumericIdentity, identity)
    stop = _decode(NumericStop, stop)
    extraction = parse_numeric_extraction(extraction)
    operation = operation_path(root, intent.admission)
    launch = _decode(
        NumericLaunch,
        read_historical_record(root, operation / "control" / "launch.json"),
    )
    if launch.intent != intent or not stop.deadline_bound_met:
        raise ProcessError("numeric_history_intent")
    for name, expected in (
        ("identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        if read_historical_record(
            root, operation / "control" / (name + ".json")
        ) != expected.model_dump(mode="json"):
            raise ProcessError("numeric_history_receipt")
    validate_numeric_journal(launch, identity, stop, extraction)
    raw = read_historical_output(
        root,
        intent.admission,
        kind="numeric",
        basename=extraction.basename,
        size_bytes=extraction.size_bytes,
        sha256=extraction.sha256,
    )
    report = (
        read_historical_output(
            root,
            intent.admission,
            kind="numeric",
            basename=extraction.report_basename,
            size_bytes=extraction.report_size_bytes,
            sha256=extraction.report_sha256,
        )
        if isinstance(extraction, NumericReportExtraction)
        else None
    )
    require_native_storage_guard(root)
    return HistoricalNumericBytes(
        intent.admission.id,
        extraction.basename,
        extraction.sha256,
        raw,
        extraction.report_sha256 if report is not None else None,
        report,
    )
