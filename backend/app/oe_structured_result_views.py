"""Bounded identities and explicitly released parent/child projections."""

import math

from sqlalchemy import select

from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.models import ResearchRun
from backend.app.oe_structured_identity import decode_candidate, decode_spec, fail
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
)
from backend.app.oe_structured_result_schemas import (
    NormalExecutionRecord,
    NormalExecutionView,
    NormalPairComparison,
)
from backend.app.services import DomainError
from packages.structured_factor_strategy.views import execution_view


def list_records(session, owner_id, run_id):
    run = session.scalar(select(ResearchRun).filter_by(id=run_id, owner_id=owner_id))
    if run is None or (run.id, run.owner_id) != (run_id, owner_id):
        raise DomainError("not_found", "Normal structured run not found", 404)
    _, config = decode_spec(session, run)
    publication = session.get(
        ExecutionSnapshotPublication, config.fixed_inputs.snapshot_id
    )
    if publication is None or (publication.owner_id, publication.factor_version_id) != (
        owner_id,
        config.fixed_inputs.factor_version_id,
    ):
        fail()
    candidates = session.scalars(
        select(OEStructuredCandidate)
        .filter_by(owner_id=owner_id, run_id=run_id)
        .order_by(OEStructuredCandidate.ordinal)
        .limit(4)
    ).all()
    executions = session.scalars(
        select(OEStructuredExecution)
        .filter_by(owner_id=owner_id, run_id=run_id)
        .limit(4)
    ).all()
    if len(candidates) > config.search.maximum_mutation_slots + 1 or len(
        executions
    ) > len(candidates):
        fail()
    indexed = {row.candidate_id: row for row in executions}
    if len(indexed) != len(executions) or set(indexed) - {row.id for row in candidates}:
        fail()
    records = []
    for candidate in candidates:
        decode_candidate(session, run, candidate)
        execution = indexed.get(candidate.id)
        if execution is not None and (
            (
                execution.owner_id,
                execution.run_id,
                execution.attempt_id,
                execution.generation,
                execution.program_version_id,
                execution.snapshot_id,
            )
            != (
                owner_id,
                run_id,
                candidate.attempt_id,
                candidate.generation,
                candidate.program_version_id,
                config.fixed_inputs.snapshot_id,
            )
        ):
            fail()
        records.append(
            NormalExecutionRecord(
                candidate_id=candidate.id,
                parent_candidate_id=candidate.parent_candidate_id,
                ordinal=candidate.ordinal,
                origin=candidate.origin,
                program_version_id=candidate.program_version_id,
                execution_id=None if execution is None else execution.id,
                snapshot_id=config.fixed_inputs.snapshot_id,
                source_run_id=publication.run_id,
            )
        )
    return tuple(records)


def project(verified):
    return NormalExecutionView(
        execution=execution_view(verified.execution),
        candidate_id=verified.candidate_id,
        measurement=verified.measurement,
    )


def compare_released(before, after):
    left, right = before.execution, after.execution
    if left.execution_id == right.execution_id or (
        left.run_id,
        left.snapshot_id,
        left.factor_version_id,
        left.snapshot_input_sha256,
        before.config_sha256,
        left.result.market_sha256,
        before.measurement.objective_sha256,
    ) != (
        right.run_id,
        right.snapshot_id,
        right.factor_version_id,
        right.snapshot_input_sha256,
        after.config_sha256,
        right.result.market_sha256,
        after.measurement.objective_sha256,
    ):
        fail()
    for key in ("adapter_manifest", "window", "engine"):
        if left.result.accounting_tables[key] != right.result.accounting_tables[key]:
            fail()
    delta = None
    if before.measurement.eligible and after.measurement.eligible:
        delta = after.measurement.combined_score - before.measurement.combined_score
        if not math.isfinite(delta):
            fail()
    return NormalPairComparison(
        before=project(before),
        after=project(after),
        score_delta_percentage_points=delta,
    )
