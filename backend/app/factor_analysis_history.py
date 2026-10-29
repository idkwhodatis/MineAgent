"""Current-owner historical analysis view; never a current process capability."""

import os
import stat
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from backend.app.analysis_historical_io import read_historical_analysis_bytes
from backend.app.core.atomic_storage import _open_directory
from backend.app.execution_snapshot_history import (
    fail,
    historical_session,
    process_history,
    read_snapshot_in_session,
    same,
)
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_analysis_models import (
    FactorAnalysisPlan,
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.factor_analysis_reconstruction import reconstruct_direct_input
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import DatasetVersion
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_snapshot_config import (
    composed_controller_in_session,
    provider_config_in_session,
    seeded_controller_in_session,
    workflow_config_in_session,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.direct_wire import read_result
from packages.qlib_adapter.objective import measure_direct_objective


def retained_reader(root):
    """Internal bounded stable nofollow reads; caller checks semantic hashes."""
    root = Path(root).absolute()

    def read(key, maximum):
        require_native_storage_guard(root)
        if (
            type(key) is not str
            or key.startswith("/")
            or "\\" in key
            or any(p in {"", ".", ".."} for p in key.split("/"))
            or type(maximum) is not int
            or not 0 < maximum <= 32 * 1024**2
        ):
            fail()
        parent = _open_directory(root / Path(key).parent)
        try:
            fd = os.open(
                Path(key).name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                before = os.fstat(fd)
                deadline = time.monotonic() + 10
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or not 0 < before.st_size <= maximum
                ):
                    fail()
                raw = bytearray()
                while chunk := os.read(fd, min(65536, maximum + 1 - len(raw))):
                    raw.extend(chunk)
                    if len(raw) > maximum or time.monotonic() >= deadline:
                        fail()
                after = os.fstat(fd)
                named = os.stat(Path(key).name, dir_fd=parent, follow_symlinks=False)

                def identity(v):
                    return (
                        v.st_dev,
                        v.st_ino,
                        v.st_mode,
                        v.st_nlink,
                        v.st_size,
                        v.st_mtime_ns,
                        v.st_ctime_ns,
                    )

                if (
                    identity(before) != identity(after)
                    or identity(after) != identity(named)
                    or len(raw) != before.st_size
                ):
                    fail()
                require_native_storage_guard(root)
                return bytes(raw)
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    return read


def row_document(row):
    return {
        c.name: (
            str(getattr(row, c.name))
            if isinstance(getattr(row, c.name), UUID)
            else getattr(row, c.name).isoformat()
            if isinstance(getattr(row, c.name), (date, datetime))
            else getattr(row, c.name)
        )
        for c in row.__table__.columns
    }


@dataclass(frozen=True)
class HistoricalDirectAnalysis:
    analysis_id: UUID
    snapshot_id: UUID
    admission_id: UUID
    result_sha256: str
    measurement_sha256: str
    result: object
    measurement: object
    qualification: str = "owned_historical_integrity_not_live_execution_authority"


def read_historical_direct_analysis(factory, owner_id, analysis_id, settings):
    with historical_session(factory, owner_id, settings) as session:
        return read_analysis_in_session(session, owner_id, analysis_id, settings)


def read_analysis_in_session(session, owner_id, analysis_id, settings):
    """Same physical integrity read under an already held historical storage guard."""
    require_native_storage_guard(settings.storage_root)
    intent = session.scalar(
        select(FactorDirectAnalysisIntent).filter_by(id=analysis_id, owner_id=owner_id)
    )
    if intent is None:
        fail()
    outcome = session.get(FactorDirectAnalysisOutcome, analysis_id)
    if outcome is None:
        fail()
    same(intent, outcome)
    snapshot = read_snapshot_in_session(session, owner_id, intent.snapshot_id, settings)
    session.execute(
        text("SELECT mineagent_direct_result_check(:id)"), {"id": analysis_id}
    )
    plan = session.get(FactorAnalysisPlan, intent.plan_id)
    publication = session.get(ExecutionSnapshotPublication, snapshot.snapshot_id)
    if plan is None or plan.owner_id != owner_id or plan.run_id != intent.run_id:
        fail()
    spec = session.get(FactorResearchSpec, plan.spec_id)
    dataset = session.get(DatasetVersion, plan.label_source_dataset_id)
    if spec is None or dataset is None:
        fail()
    from backend.app.rdagent_snapshot_config import declared_upload_in_session

    value, projection, objective = reconstruct_direct_input(
        plan=row_document(plan),
        intent=row_document(intent),
        publication=row_document(publication),
        spec=row_document(spec),
        dataset=row_document(dataset),
        read_bytes=retained_reader(settings.storage_root),
        provider_config=provider_config_in_session(session),
        composed_controller=composed_controller_in_session(session),
        seeded_controller=seeded_controller_in_session(session),
        workflow_config=workflow_config_in_session(session),
        declared_upload=declared_upload_in_session(session),
    )
    _, receipts = process_history(session, outcome, outcome.admission_id)
    physical = read_historical_analysis_bytes(
        settings.storage_root,
        intent=receipts["launch_intent"].payload,
        identity=receipts["launch_identity"].payload,
        stop=receipts["stop"].payload,
        extraction=receipts["extraction"].payload,
    )
    if (
        physical.analysis_id != analysis_id
        or physical.snapshot_id != intent.snapshot_id
        or physical.protocol_sha256 != value.protocol.sha256
    ):
        fail()
    result = read_result(physical.result_bytes, value)
    measurement = measure_direct_objective(objective, projection, result)
    if (
        physical.sha256 != outcome.result_sha256
        or len(physical.result_bytes) != outcome.result_size_bytes
        or outcome.result_canonical.encode("ascii") != physical.result_bytes
        or outcome.result != result.model_dump(mode="json")
        or outcome.measurement != measurement.model_dump(mode="json")
        or outcome.measurement_sha256 != digest(measurement)
        or outcome.measurement_canonical.encode("ascii") != canonical_bytes(measurement)
    ):
        fail()
    return HistoricalDirectAnalysis(
        analysis_id,
        intent.snapshot_id,
        outcome.admission_id,
        outcome.result_sha256,
        outcome.measurement_sha256,
        result,
        measurement,
    )
