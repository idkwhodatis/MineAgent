"""Explicit owner disclosure of actual normal027 results, never live authority.

Read immutable physical history privately, then bind exact normal execution and
source commitments under the owner lock before committing existing-ledger
exposures. A model proposal, successful status or SDK response is not a release.
"""

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.engine_result_release import snapshot_bindings
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.experiment_services import record_exposure
from backend.app.factor_models import FactorVersion
from backend.app.models import DatasetVersion, DataUsageExposure, ResearchRun
from backend.app.oe_structured_identity import decode_candidate, decode_spec, fail
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredEngineOutcome,
    OEStructuredExecution,
)
from backend.app.services import DomainError, advisory_lock
from packages.factors.contracts import DateWindow, canonical_bytes, digest


def release_key(execution_id, role):
    return f"engine-result:oe_structured_execution:{execution_id}:{role}:v1"


def owned_execution(session, owner_id, run_id, execution_id):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        fail()
    run = session.scalar(select(ResearchRun).filter_by(id=run_id, owner_id=owner_id))
    execution = session.scalar(
        select(OEStructuredExecution).filter_by(
            id=execution_id, owner_id=owner_id, run_id=run_id
        )
    )
    if run is None or execution is None:
        raise DomainError("not_found", "Normal structured result not found", 404)
    if (run.id, run.owner_id) != (run_id, owner_id) or (
        execution.id,
        execution.owner_id,
        execution.run_id,
    ) != (execution_id, owner_id, run_id):
        fail()
    _, config = decode_spec(session, run)
    if run.status not in {"succeeded", "failed", "cancelled", "interrupted"}:
        fail()
    return run, execution, config


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
                "Explicit release of each exact normal execution is required",
                409,
            )


def bindings(session, owner_id, run_id, execution_id, verified):
    run, execution, config = owned_execution(session, owner_id, run_id, execution_id)
    candidate = session.get(OEStructuredCandidate, execution.candidate_id)
    outcome = session.scalar(
        select(OEStructuredEngineOutcome).filter_by(
            owner_id=owner_id, run_id=run_id, execution_id=execution_id
        )
    )
    if candidate is None or outcome is None:
        fail()
    decode_candidate(session, run, candidate)
    session.execute(text("SELECT mineagent_oes_graph_check(:id)"), {"id": run_id})
    value = verified.execution
    if (
        (
            value.execution_id,
            value.run_id,
            value.structured_strategy_version_id,
            value.snapshot_id,
            value.admission_id,
            value.result_sha256,
            verified.candidate_id,
            verified.config_sha256,
        )
        != (
            execution.id,
            run.id,
            execution.program_version_id,
            execution.snapshot_id,
            outcome.admission_id,
            outcome.result_sha256,
            candidate.id,
            digest(config),
        )
        or (
            outcome.owner_id,
            outcome.run_id,
            outcome.attempt_id,
            outcome.generation,
            outcome.candidate_id,
        )
        != (owner_id, run_id, execution.attempt_id, execution.generation, candidate.id)
        or candidate.program_version_id != execution.program_version_id
        or value.factor_version_id != config.fixed_inputs.factor_version_id
        or execution.snapshot_id != config.fixed_inputs.snapshot_id
        or value.snapshot_input_sha256 != config.fixed_inputs.snapshot_input_sha256
        or value.result.strategy_input_sha256 != execution.strategy_input_sha256
        or value.result.market_sha256 != execution.market_sha256
        or outcome.result_canonical != canonical_bytes(value.result).decode("ascii")
        or outcome.result_sha256 != digest(value.result)
        or outcome.measurement_canonical
        != canonical_bytes(verified.measurement).decode("ascii")
        or outcome.measurement_sha256 != verified.measurement_sha256
        or verified.measurement_sha256 != digest(verified.measurement)
        or outcome.objective_sha256 != digest(config.search.objective)
    ):
        fail()
    publication = session.get(ExecutionSnapshotPublication, execution.snapshot_id)
    version = session.get(FactorVersion, value.factor_version_id)
    if (
        publication is None
        or version is None
        or (publication.owner_id, publication.run_id, publication.factor_version_id)
        != (owner_id, value.execution_snapshot_run_id, value.factor_version_id)
    ):
        fail()
    if (
        version.owner_id,
        version.factor_id,
        version.parent_version_id,
        version.code_sha256,
        version.definition_sha256,
    ) != (
        owner_id,
        value.factor_id,
        value.parent_factor_version_id,
        value.factor_code_sha256,
        digest(value.factor_definition),
    ):
        fail()
    source = session.scalar(
        select(ResearchRun).filter_by(id=publication.run_id, owner_id=owner_id)
    )
    if source is None:
        fail()
    factor_dataset, source_evidence = snapshot_bindings(
        session, owner_id, source, publication
    )
    if source_evidence["input_sha256"] != value.snapshot_input_sha256:
        fail()
    market = session.get(DatasetVersion, config.fixed_inputs.dataset_version_id)
    if market is None or (market.owner_id, market.content_sha256) != (
        owner_id,
        config.fixed_inputs.dataset_sha256,
    ):
        fail()
    evidence = {
        "schema_version": "mineagent.oe-structured-result-release.v1",
        "run_id": str(run_id),
        "execution_id": str(execution_id),
        "candidate_id": str(candidate.id),
        "structured_strategy_version_id": str(execution.program_version_id),
        "factor_version_id": str(value.factor_version_id),
        "factor_id": str(value.factor_id),
        "execution_snapshot_id": str(execution.snapshot_id),
        "snapshot_source": source_evidence,
        "config_sha256": verified.config_sha256,
        "program_sha256": candidate.program_sha256,
        "strategy_input_sha256": execution.strategy_input_sha256,
        "market_sha256": execution.market_sha256,
        "result_sha256": outcome.result_sha256,
        "measurement_sha256": verified.measurement_sha256,
        "objective_sha256": outcome.objective_sha256,
        "extent": "conservative_full_retained_dataset_including_history_and_forward_labels",
        "qualification": "fixed_snapshot_comparison_not_causal_gain",
    }
    return run, (("factor", factor_dataset, evidence), ("market", market, evidence))


def disclose(factory, settings, *, owner_id, run_id, execution_ids, create_release):
    ids = tuple(execution_ids)
    if (
        type(create_release) is not bool
        or not 1 <= len(ids) <= 2
        or len(set(ids)) != len(ids)
    ):
        raise DomainError(
            "invalid_structured_pair",
            "One or two distinct exact executions required",
            422,
        )
    with factory() as session:
        for execution_id in ids:
            owned_execution(session, owner_id, run_id, execution_id)
            if not create_release:
                require_receipts(session, owner_id, execution_id)
    from backend.app.oe_structured_result_history import read_result

    verified = tuple(
        read_result(factory, settings, owner_id=owner_id, execution_id=key)
        for key in ids
    )
    exposures = []
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory.begin() as session,
    ):
        advisory_lock(session, f"submit:{owner_id}")
        for execution_id, value in zip(ids, verified, strict=True):
            run, items = bindings(session, owner_id, run_id, execution_id, value)
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
                ) != (owner_id, run_id, run.experiment_id, run.split_version_id):
                    fail()
                exposures.append(row.id)
    return verified, tuple(exposures)
