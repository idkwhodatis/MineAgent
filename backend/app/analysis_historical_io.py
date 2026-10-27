"""Historical result bytes only, never live interpreter or launch authority.

The outer owner-authorized historical reader must first resolve an immutable
terminal database outcome and its exact scoped receipt chain/full settlement.
This helper checks those supplied persisted records against protected files in
the CURRENT storage tree. Original execution root identity remains immutable
history, not a requirement that a restored directory reuse the old inode.
"""

from dataclasses import dataclass
from uuid import UUID

from backend.app.factor_native_io import require_native_storage_guard
from packages.research_runtime.analysis_process_journal import (
    AnalysisExtraction,
    AnalysisIdentity,
    AnalysisIntent,
    AnalysisLaunch,
    AnalysisStop,
    validate_analysis_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.analysis_journal import read_opaque_output
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import operation_path


@dataclass(frozen=True)
class HistoricalAnalysisBytes:
    analysis_id: UUID
    snapshot_id: UUID
    protocol_sha256: str
    sha256: str
    result_bytes: bytes
    qualification: str = "historical_integrity_not_live_authority"


def _decode(cls, value):
    if isinstance(value, cls):
        value = value.model_dump(mode="json")
    return cls.model_validate_json(canonical(value))


def read_historical_analysis_bytes(root, *, intent, identity, stop, extraction):
    """Re-read exact stopped history; no caller flag alters the live reader.

    Inputs are internally resolved persisted payloads, not request-body records.
    This function alone checks integrity, NOT owner/terminal database authority.
    Its distinct data-only output must never be accepted by an execution issuer.
    """
    require_native_storage_guard(root)
    intent = _decode(AnalysisIntent, intent)
    identity = _decode(AnalysisIdentity, identity)
    stop = _decode(AnalysisStop, stop)
    extraction = _decode(AnalysisExtraction, extraction)
    admission = intent.admission
    operation = operation_path(root, admission)
    launch = _decode(
        AnalysisLaunch, read_numeric_record(operation / "control" / "launch.json")
    )
    if launch.intent != intent:
        raise ProcessError("analysis_history_intent")
    for name, expected in (
        ("identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        if read_numeric_record(
            operation / "control" / (name + ".json")
        ) != expected.model_dump(mode="json"):
            raise ProcessError("analysis_history_receipt")
    validate_analysis_journal(launch, identity, stop, extraction)
    raw = read_opaque_output(root, admission, extraction)
    require_native_storage_guard(root)
    return HistoricalAnalysisBytes(
        intent.inputs.analysis_id,
        intent.inputs.snapshot_id,
        intent.inputs.protocol_sha256,
        extraction.sha256,
        raw,
    )
