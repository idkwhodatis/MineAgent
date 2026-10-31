"""Owner-scoped normal OE reverse usage; identity discovery is not disclosure."""

from sqlalchemy import and_, select, text

from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_models import Factor, FactorVersion
from backend.app.factor_services import owned
from backend.app.models import ResearchRun
from backend.app.oe_structured_identity import fail
from backend.app.oe_structured_models import (
    OEStructuredEngineOutcome,
    OEStructuredExecution,
)
from backend.app.oe_structured_result_release import owned_execution
from backend.app.oe_structured_result_views import list_records
from backend.app.services import DomainError
from backend.app.structured_result_routes import (
    StructuredExecutionRecord,
    StructuredExecutionRecords,
)


def usage_query(owner_id, version_id, offset):
    execution = OEStructuredExecution
    publication = ExecutionSnapshotPublication
    outcome = OEStructuredEngineOutcome
    return (
        select(execution)
        .join(
            publication,
            and_(
                publication.id == execution.snapshot_id,
                publication.owner_id == execution.owner_id,
            ),
        )
        .join(
            ResearchRun,
            and_(
                ResearchRun.id == execution.run_id,
                ResearchRun.owner_id == execution.owner_id,
            ),
        )
        .join(
            outcome,
            and_(
                outcome.execution_id == execution.id,
                outcome.owner_id == execution.owner_id,
                outcome.run_id == execution.run_id,
                outcome.attempt_id == execution.attempt_id,
                outcome.generation == execution.generation,
                outcome.candidate_id == execution.candidate_id,
            ),
        )
        .where(
            execution.owner_id == owner_id,
            publication.factor_version_id == version_id,
            ResearchRun.status.in_(("succeeded", "failed", "cancelled", "interrupted")),
        )
        .order_by(execution.created_at.desc(), execution.id.desc())
        .offset(offset)
        .limit(50)
    )


def factor_usage(session, owner_id, factor_id, version_id, offset=0):
    factor = owned(session, Factor, owner_id, factor_id)
    version = owned(session, FactorVersion, owner_id, version_id)
    if version.factor_id != factor.id:
        raise DomainError("not_found", "Factor version not found", 404)
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        raise DomainError(
            "oe_structured_history_schema_unavailable",
            "Normal structured history unavailable",
            409,
        )
    rows = session.scalars(usage_query(owner_id, version_id, offset)).all()
    result, runs = [], {}
    for row in rows:
        _, exact, config = owned_execution(session, owner_id, row.run_id, row.id)
        if (
            exact.id,
            exact.owner_id,
            config.fixed_inputs.factor_version_id,
            config.fixed_inputs.snapshot_id,
        ) != (row.id, owner_id, version.id, row.snapshot_id):
            fail()
        if row.run_id not in runs:
            runs[row.run_id] = list_records(session, owner_id, row.run_id)
        matches = [
            record for record in runs[row.run_id] if record.execution_id == row.id
        ]
        if len(matches) != 1 or (
            matches[0].candidate_id,
            matches[0].program_version_id,
            matches[0].snapshot_id,
        ) != (row.candidate_id, row.program_version_id, row.snapshot_id):
            fail()
        result.append(
            StructuredExecutionRecord(
                execution_id=row.id,
                run_id=row.run_id,
                factor_id=factor.id,
                factor_version_id=version.id,
                factor_version_ordinal=version.ordinal,
                parent_factor_version_id=version.parent_version_id,
                execution_snapshot_id=row.snapshot_id,
                structured_strategy_version_id=row.program_version_id,
            )
        )
    return StructuredExecutionRecords(data=tuple(result))
