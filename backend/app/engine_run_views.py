"""Owner-scoped persisted RD metadata, without reading artifacts or issuing authority."""

from sqlalchemy import and_, func, select

from backend.app.engine_run_schemas import (
    EngineHistoryItem,
    EngineRunDetail,
    EngineRunSummary,
)
from backend.app.execution_snapshot_models import (
    ExecutionEvaluationClaim,
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
    FactorExecutionEvaluationOutcome,
)
from backend.app.factor_models import FactorVersion
from backend.app.models import ResearchRun
from backend.app.rdagent_models import (
    FactorEvaluationOutcome,
    FactorEvaluationStart,
    FactorGenerationOutcome,
    FactorGenerationSource,
    FactorGenerationStart,
)
from backend.app.services import DomainError, owned_run
from packages.rdagent_adapter.persisted_identity import require_source_identity


def rd_config(run):
    try:
        return require_source_identity(run)
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "not_found", "Supported engine history not found", 404
        ) from None


def owned_engine_run(session, run_id, owner_id):
    run = owned_run(session, run_id, owner_id)
    rd_config(run)
    return run


def summary(run):
    config = rd_config(run)
    return EngineRunSummary(
        config_schema=config.schema_version,
        id=run.id,
        status=run.status,
        generation=run.generation,
        dataset_version_id=run.dataset_version_id,
        task_name=config.task.name,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def detail(run):
    config = rd_config(run)
    return EngineRunDetail(
        **summary(run).model_dump(),
        config_sha256=config.sha256,
        task_description=config.task.description,
        max_iterations=config.max_iterations,
        budget_limits=config.budget.model_dump(),
        error=run.error,
    )


def list_history_runs(session, owner_id, offset, limit):
    clause = and_(
        ResearchRun.owner_id == owner_id,
        ResearchRun.purpose == "factor_research",
        ResearchRun.engine == "rdagent",
    )
    rows = session.scalars(
        select(ResearchRun)
        .where(clause)
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "data": [summary(row) for row in rows],
        "count": session.scalar(
            select(func.count()).select_from(ResearchRun).where(clause)
        ),
    }


def history_query(run):
    start, source, outcome = (
        FactorGenerationStart,
        FactorGenerationSource,
        FactorGenerationOutcome,
    )
    evaluation, result = FactorEvaluationStart, FactorEvaluationOutcome
    claim, publication = ExecutionEvaluationClaim, ExecutionSnapshotPublication
    completed, execution = ExecutionSnapshotOutcome, FactorExecutionEvaluationOutcome

    # All optional joins retain the same owner/run/attempt/generation. Absence
    # remains null; a source or evaluation ticket is never a completed snapshot.
    def scope(model):
        return and_(
            model.owner_id == start.owner_id,
            model.run_id == start.run_id,
            model.attempt_id == start.attempt_id,
            model.generation == start.generation,
        )

    return (
        select(
            start,
            source,
            outcome,
            evaluation,
            result,
            FactorVersion.factor_id,
            claim,
            publication,
            completed,
            execution,
        )
        .select_from(start)
        .outerjoin(source, and_(source.start_id == start.id, scope(source)))
        .outerjoin(outcome, and_(outcome.start_id == start.id, scope(outcome)))
        .outerjoin(evaluation, and_(evaluation.start_id == start.id, scope(evaluation)))
        .outerjoin(result, and_(result.evaluation_id == evaluation.id, scope(result)))
        .outerjoin(claim, and_(claim.evaluation_id == evaluation.id, scope(claim)))
        .outerjoin(
            publication,
            and_(publication.evaluation_id == evaluation.id, scope(publication)),
        )
        .outerjoin(
            completed,
            and_(completed.publication_id == publication.id, scope(completed)),
        )
        .outerjoin(
            execution, and_(execution.evaluation_id == evaluation.id, scope(execution))
        )
        .outerjoin(
            FactorVersion,
            and_(
                FactorVersion.id == source.factor_version_id,
                FactorVersion.owner_id == start.owner_id,
            ),
        )
        .where(start.owner_id == run.owner_id, start.run_id == run.id)
        .order_by(start.created_at, start.id)
    )


def snapshot_projection(legacy, claim, publication, completed, execution):
    """Dispatch exact persisted result kinds; never alias execution IDs to legacy."""

    def require(condition):
        if not condition:
            raise DomainError(
                "engine_history_inconsistent",
                "Persisted evaluation identities differ",
                409,
            )

    require(not (legacy and execution))
    if legacy:
        require(
            claim is not None
            and claim.consumer_kind == "legacy_result"
            and claim.consumer_id == legacy.id
        )
    if execution:
        require(
            claim is not None
            and claim.consumer_kind == "execution_result"
            and claim.consumer_id == execution.id
        )
        require(publication is not None and completed is not None)
        require(execution.snapshot_id == publication.id == completed.publication_id)
        require(
            (
                execution.snapshot_input_sha256,
                execution.snapshot_output_sha256,
                execution.snapshot_manifest_sha256,
            )
            == (
                completed.input_sha256,
                completed.output_sha256,
                completed.manifest_sha256,
            )
        )
    if completed:
        require(
            publication is not None
            and completed.publication_id == publication.id
            and completed.manifest_sha256 == publication.manifest_sha256
        )
    if publication:
        require(
            publication.manifest.get("schema_version")
            == "mineagent.factor-execution-snapshot.v1"
        )
    if claim:
        require(claim.consumer_kind in {"legacy_result", "execution_result"})
        require(not (claim.consumer_kind == "legacy_result" and execution))
        require(not (claim.consumer_kind == "execution_result" and legacy))
    return {
        "evaluation_protocol": claim.consumer_kind if claim else None,
        "execution_snapshot_publication_id": publication.id if publication else None,
        "execution_snapshot_id": completed.publication_id if completed else None,
        "execution_snapshot_schema": publication.manifest["schema_version"]
        if publication
        else None,
        "execution_snapshot_state": "completed"
        if completed
        else ("publication_pending" if publication else None),
        "execution_snapshot_input_sha256": completed.input_sha256
        if completed
        else None,
        "execution_snapshot_output_sha256": completed.output_sha256
        if completed
        else None,
        "execution_snapshot_manifest_sha256": completed.manifest_sha256
        if completed
        else None,
    }


def project_history(row):
    (
        start,
        source,
        outcome,
        evaluation,
        result,
        factor_id,
        claim,
        publication,
        completed,
        execution,
    ) = row
    projection = snapshot_projection(result, claim, publication, completed, execution)
    return EngineHistoryItem(
        id=start.id,
        attempt_id=start.attempt_id,
        generation=start.generation,
        logical_step=start.logical_step,
        created_at=start.created_at,
        source_id=source.id if source else None,
        source_sha256=source.source_sha256 if source else None,
        factor_version_id=source.factor_version_id if source else None,
        factor_id=factor_id,
        generation_outcome=outcome.outcome if outcome else None,
        error_code=outcome.error_code if outcome else None,
        evaluated_source_id=outcome.evaluated_source_id if outcome else None,
        adopted_source_id=outcome.adopted_source_id if outcome else None,
        continuation_source_id=outcome.continuation_source_id if outcome else None,
        evaluation_id=evaluation.id if evaluation else None,
        evaluation_status=execution.status
        if execution
        else (result.status if result else None),
        snapshot_id=result.snapshot_id if result else None,
        snapshot_input_sha256=result.snapshot_input_sha256 if result else None,
        snapshot_output_sha256=result.snapshot_output_sha256 if result else None,
        **projection,
    )


def history(session, run, offset, limit):
    rows = session.execute(history_query(run).offset(offset).limit(limit)).all()
    count = session.scalar(
        select(func.count())
        .select_from(FactorGenerationStart)
        .where(
            FactorGenerationStart.owner_id == run.owner_id,
            FactorGenerationStart.run_id == run.id,
        )
    )
    return {"data": [project_history(row) for row in rows], "count": count}
