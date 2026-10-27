"""Current-owner terminal structured result view, never launch/replay authority."""

import hashlib
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.execution_snapshot_history import process_history, same
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_models import Factor, FactorVersion
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import ResearchRun
from backend.app.structured_factor_models import (
    StructuredExecution,
    StructuredExecutionOutcome,
)
from backend.app.structured_factor_results import _check_rows, _expected, _reservation
from backend.app.structured_factor_services import (
    _exposure,
    decode_structured_spec,
    fail,
    reconstruct_retained_input,
)
from backend.app.structured_historical_io import read_historical_structured_bytes
from backend.app.structured_process_services import (
    as_structured_admission,
    require_structured_capability,
)
from packages.factors.contracts import FactorDefinition
from packages.structured_factor_strategy.wire import (
    StructuredResult,
    read_result,
    write_input,
)
from runtimes.structured_factor_engine.journal import StructuredIntent


@dataclass(frozen=True)
class HistoricalStructuredExecution:
    execution_id: UUID
    run_id: UUID
    structured_strategy_version_id: UUID
    factor_version_id: UUID
    factor_id: UUID
    parent_factor_version_id: UUID | None
    snapshot_id: UUID
    execution_snapshot_run_id: UUID
    admission_id: UUID
    result_sha256: str
    result: StructuredResult
    factor_definition: FactorDefinition
    factor_code_sha256: str
    snapshot_input_sha256: str
    qualification: str = (
        "owned_historical_closed_transform_not_live_execution_authority"
    )


def read_historical_structured_execution(factory, owner_id, execution_id, settings):
    """Private verified immutable buffer; public disclosure still requires ledger release."""
    if type(owner_id) is not UUID or type(execution_id) is not UUID:
        fail()
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        if os.path.lexists(settings.storage_root / ".restore-incomplete"):
            fail()
        with factory.begin() as session:
            require_structured_capability(session)
            execution = session.scalar(
                select(StructuredExecution).filter_by(
                    id=execution_id, owner_id=owner_id
                )
            )
            if execution is None:
                fail()
            run = session.scalar(
                select(ResearchRun).filter_by(id=execution.run_id, owner_id=owner_id)
            )
            if run is None or run.status not in {
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
            }:
                fail()
            _, _, config, program = decode_structured_spec(session, run)
            outcome = session.get(StructuredExecutionOutcome, execution_id)
            if outcome is None:
                fail()
            same(outcome, execution)
            session.execute(
                text("SELECT mineagent_structured_outcome_check(:id)"),
                {"id": execution_id},
            )
            value, market, dataset = reconstruct_retained_input(
                session,
                execution,
                execution_id,
                execution.snapshot_id,
                config,
                program,
                settings,
            )
            publication = session.get(ExecutionSnapshotPublication, execution.snapshot_id)
            if (
                publication is None
                or publication.owner_id != owner_id
                or publication.factor_version_id != execution.factor_version_id
            ):
                fail()
            version = session.get(FactorVersion, execution.factor_version_id)
            if (
                version is None
                or version.owner_id != owner_id
                or (
                    version.definition_sha256
                    != value.snapshot.input.version.definition_sha256
                    or version.code_sha256 != value.snapshot.input.version.code_sha256
                )
            ):
                fail()
            factor = session.get(Factor, version.factor_id)
            if factor is None or factor.owner_id != owner_id:
                fail()
            if version.parent_version_id is not None:
                parent = session.get(FactorVersion, version.parent_version_id)
                if parent is None or parent.owner_id != owner_id:
                    fail()
            _exposure(session, execution, run, config, dataset, create=False)
            raw = write_input(value)
            if (
                raw.decode("ascii"),
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                hashlib.sha256(market).hexdigest(),
                len(market),
            ) != (
                execution.strategy_input_canonical,
                execution.strategy_input_sha256,
                execution.strategy_input_size_bytes,
                execution.market_sha256,
                execution.market_size_bytes,
            ):
                fail()
            admission, receipts = process_history(
                session, outcome, outcome.admission_id
            )
            admission = as_structured_admission(admission)
            intent = StructuredIntent.model_validate(receipts["launch_intent"].payload)
            if (
                intent.admission != admission
                or intent.inputs.execution_id != execution_id
                or intent.inputs.snapshot_id != execution.snapshot_id
                or intent.inputs.structured_strategy_version_id
                != execution.structured_strategy_version_id
                or intent.inputs.strategy_input_sha256
                != execution.strategy_input_sha256
                or intent.inputs.market_sha256 != execution.market_sha256
            ):
                fail()
            physical = read_historical_structured_bytes(
                settings.storage_root,
                intent=intent,
                identity=receipts["launch_identity"].payload,
                stop=receipts["stop"].payload,
                extraction=receipts["extraction"].payload,
            )
            result = read_result(physical.result_bytes, value)
            if (
                physical.execution_id != execution_id
                or physical.snapshot_id != execution.snapshot_id
                or physical.strategy_input_sha256 != execution.strategy_input_sha256
                or physical.sha256 != outcome.result_sha256
                or physical.result_bytes.decode("ascii") != outcome.result_canonical
                or result.model_dump(mode="json") != outcome.result
            ):
                fail()
            # Reuse exact row/full-charge comparisons, never the live process reader.
            from types import SimpleNamespace

            receipt = SimpleNamespace(
                admission=admission,
                execution_id=execution_id,
                launch_identity_receipt_id=receipts["launch_identity"].id,
                launch_identity_receipt_hash=receipts["launch_identity"].payload_sha256,
                stop_receipt_id=receipts["stop"].id,
                stop_receipt_hash=receipts["stop"].payload_sha256,
                extraction_receipt_id=receipts["extraction"].id,
                extraction_receipt_hash=receipts["extraction"].payload_sha256,
                sha256=physical.sha256,
                size_bytes=len(physical.result_bytes),
                result_bytes=physical.result_bytes,
            )
            if any(
                getattr(outcome, k) != v for k, v in _expected(receipt, result).items()
            ):
                fail()
            _reservation(session, execution, receipt, completed=True)
            _check_rows(session, execution, result)
            return HistoricalStructuredExecution(
                execution_id,
                run.id,
                execution.structured_strategy_version_id,
                execution.factor_version_id,
                version.factor_id,
                version.parent_version_id,
                execution.snapshot_id,
                publication.run_id,
                admission.id,
                physical.sha256,
                result,
                value.snapshot.input.definition,
                value.snapshot.input.version.code_sha256,
                value.snapshot.input_sha256,
            )
