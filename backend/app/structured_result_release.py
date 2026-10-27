"""Two-phase owned025 disclosure, not a live execution or restoration grant.

Unregistered until coherent025 capability/migration acceptance. Privately verify
retained bytes first, then recheck immutable commitments and commit existing-ledger
exposures under owner-first locking before returning any value or comparison flag.
"""

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.engine_result_release import snapshot_bindings
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.experiment_services import record_exposure
from backend.app.factor_models import FactorVersion
from backend.app.models import DatasetVersion, DataUsageExposure, ResearchRun
from backend.app.services import DomainError, advisory_lock
from backend.app.structured_factor_history import read_historical_structured_execution
from backend.app.structured_factor_models import (
    StructuredExecution,
    StructuredExecutionOutcome,
)
from backend.app.structured_factor_services import decode_structured_spec
from backend.app.structured_process_services import require_structured_capability
from packages.factors.contracts import DateWindow, digest


def inconsistent():
    raise DomainError(
        "structured_result_inconsistent",
        "Owned immutable structured result differs",
        409,
    )


def release_key(execution_id, role):
    return f"engine-result:structured_execution:{execution_id}:{role}:v1"


def owned_execution(session, owner_id, run_id, execution_id):
    run = session.scalar(select(ResearchRun).filter_by(id=run_id, owner_id=owner_id))
    execution = session.scalar(
        select(StructuredExecution).filter_by(
            id=execution_id, owner_id=owner_id, run_id=run_id
        )
    )
    if run is None or execution is None:
        raise DomainError("not_found", "Structured result not found", 404)
    require_structured_capability(session)
    decode_structured_spec(session, run)
    if run.status not in {"succeeded", "failed", "cancelled", "interrupted"}:
        inconsistent()
    return run, execution


def require_receipts(session, owner_id, execution_id):
    for role in ("factor", "market"):
        if (
            session.scalar(
                select(DataUsageExposure.id).filter_by(
                    owner_id=owner_id, source_key=release_key(execution_id, role)
                )
            )
            is None
        ):
            raise DomainError(
                "engine_result_release_required",
                "Explicit release of each exact structured execution is required",
                409,
            )


def bindings(session, owner_id, run_id, execution_id, verified):
    run, execution = owned_execution(session, owner_id, run_id, execution_id)
    _, _, config, program = decode_structured_spec(session, run)
    outcome = session.get(StructuredExecutionOutcome, execution_id)
    if outcome is None:
        inconsistent()
    session.execute(
        text("SELECT mineagent_structured_outcome_check(:id)"), {"id": execution_id}
    )
    if (
        verified.execution_id,
        verified.run_id,
        verified.structured_strategy_version_id,
        verified.factor_version_id,
        verified.snapshot_id,
        verified.admission_id,
        verified.result_sha256,
        verified.result.strategy_input_sha256,
        verified.result.market_sha256,
    ) != (
        execution.id,
        run.id,
        execution.structured_strategy_version_id,
        execution.factor_version_id,
        execution.snapshot_id,
        outcome.admission_id,
        outcome.result_sha256,
        execution.strategy_input_sha256,
        execution.market_sha256,
    ) or digest(verified.result) != outcome.result_sha256:
        inconsistent()
    if (outcome.owner_id, outcome.run_id, outcome.attempt_id, outcome.generation) != (
        owner_id,
        run.id,
        execution.attempt_id,
        execution.generation,
    ):
        inconsistent()
    publication = session.get(ExecutionSnapshotPublication, execution.snapshot_id)
    version = session.get(FactorVersion, execution.factor_version_id)
    if (
        publication is None
        or version is None
        or publication.owner_id != owner_id
        or publication.run_id != verified.execution_snapshot_run_id
        or publication.factor_version_id != execution.factor_version_id
    ):
        inconsistent()
    if (
        version.owner_id,
        version.factor_id,
        version.parent_version_id,
        version.code_sha256,
        version.definition_sha256,
    ) != (
        owner_id,
        verified.factor_id,
        verified.parent_factor_version_id,
        verified.factor_code_sha256,
        digest(verified.factor_definition),
    ):
        inconsistent()
    source_run = session.scalar(
        select(ResearchRun).filter_by(id=publication.run_id, owner_id=owner_id)
    )
    if source_run is None:
        inconsistent()
    factor_dataset, source_evidence = snapshot_bindings(
        session, owner_id, source_run, publication
    )
    if source_evidence["input_sha256"] != verified.snapshot_input_sha256:
        inconsistent()
    market_dataset = session.get(DatasetVersion, config.dataset_version_id)
    if (
        market_dataset is None
        or market_dataset.owner_id != owner_id
        or market_dataset.content_sha256 != config.dataset_sha256
    ):
        inconsistent()
    evidence = {
        "schema_version": "mineagent.structured-result-release.v1",
        "run_id": str(run.id),
        "execution_id": str(execution_id),
        "structured_strategy_version_id": str(execution.structured_strategy_version_id),
        "factor_id": str(verified.factor_id),
        "factor_version_id": str(execution.factor_version_id),
        "parent_factor_version_id": str(verified.parent_factor_version_id)
        if verified.parent_factor_version_id
        else None,
        "factor_definition_sha256": digest(verified.factor_definition),
        "factor_code_sha256": verified.factor_code_sha256,
        "execution_snapshot_id": str(execution.snapshot_id),
        "snapshot_source": source_evidence,
        "config_sha256": digest(config),
        "program_sha256": digest(program),
        "strategy_input_sha256": execution.strategy_input_sha256,
        "market_sha256": execution.market_sha256,
        "result_sha256": outcome.result_sha256,
        "extent": "conservative_full_retained_dataset_including_history_and_forward_labels",
        "qualification": "historical_disclosure_not_live_authority_labels_not_trading_input",
    }
    return run, [
        ("factor", factor_dataset, evidence),
        ("market", market_dataset, evidence),
    ]


def disclose_structured(
    factory, settings, *, owner_id, run_id, execution_ids, create_release
):
    ids = tuple(execution_ids)
    if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids):
        raise DomainError(
            "invalid_structured_pair", "One or two distinct executions required", 422
        )
    with factory() as session:
        for execution_id in ids:
            owned_execution(session, owner_id, run_id, execution_id)
            if not create_release:
                require_receipts(session, owner_id, execution_id)
    verified = tuple(
        read_historical_structured_execution(factory, owner_id, execution_id, settings)
        for execution_id in ids
    )
    exposures = []
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory.begin() as session,
    ):
        advisory_lock(session, f"submit:{owner_id}")
        for execution_id, buffer in zip(ids, verified, strict=True):
            run, items = bindings(session, owner_id, run_id, execution_id, buffer)
            if not create_release:
                require_receipts(session, owner_id, execution_id)
            for role, dataset, evidence in items:
                row = record_exposure(
                    session,
                    owner_id=owner_id,
                    dataset=dataset,
                    run=run,
                    window=DateWindow(start=dataset.start_date, end=dataset.end_date),
                    purpose="engine_structured_execution_release",
                    source_key=release_key(execution_id, role),
                    provenance={
                        **evidence,
                        "role": role,
                        "dataset_version_id": str(dataset.id),
                        "dataset_content_sha256": dataset.content_sha256,
                    },
                )
                if (
                    row.owner_id,
                    row.run_id,
                    row.experiment_id,
                    row.split_version_id,
                ) != (owner_id, run.id, run.experiment_id, run.split_version_id):
                    inconsistent()
                exposures.append(row.id)
    return verified, tuple(exposures)
