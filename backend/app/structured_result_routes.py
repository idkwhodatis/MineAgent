"""Owned025 historical routes; exact installed capability and disclosure required."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.services import DomainError
from backend.app.structured_result_release import disclose_structured
from packages.structured_factor_strategy.views import (
    StructuredExecutionView,
    StructuredPairComparison,
    compare_released_pair,
    execution_view,
)

router = APIRouter()


class StructuredRelease(BaseModel):
    run_id: UUID
    execution_id: UUID
    exposure_ids: tuple[UUID, ...]
    qualification: Literal["exposure_record_not_execution_authority"] = (
        "exposure_record_not_execution_authority"
    )
    extent: Literal["full_retained_factor_and_market_datasets"] = (
        "full_retained_factor_and_market_datasets"
    )


def access(request, user, run_id, ids, *, release=False):
    return disclose_structured(
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        run_id=run_id,
        execution_ids=ids,
        create_release=release,
    )


@router.post(
    "/engine-runs/{run_id}/structured-executions/{execution_id}/release",
    response_model=StructuredRelease,
)
def release_execution(
    run_id: UUID, execution_id: UUID, user: CurrentUser, request: Request
):
    _, exposure_ids = access(request, user, run_id, (execution_id,), release=True)
    return StructuredRelease(
        run_id=run_id, execution_id=execution_id, exposure_ids=exposure_ids
    )


@router.get(
    "/engine-runs/{run_id}/structured-executions/{execution_id}",
    response_model=StructuredExecutionView,
)
def read_execution(
    run_id: UUID, execution_id: UUID, user: CurrentUser, request: Request
):
    buffers, _ = access(request, user, run_id, (execution_id,))
    return execution_view(buffers[0])


@router.get(
    "/engine-runs/{run_id}/structured-comparison",
    response_model=StructuredPairComparison,
)
def read_comparison(
    run_id: UUID, before: UUID, after: UUID, user: CurrentUser, request: Request
):
    buffers, _ = access(request, user, run_id, (before, after))
    try:
        return compare_released_pair(*buffers)
    except (ValueError, TypeError, KeyError):
        raise DomainError(
            "invalid_structured_pair",
            "Exact same market/program comparison required",
            409,
        ) from None


class StructuredExecutionRecord(BaseModel):
    """Identity-only discovery; no result values, measurements or change flags."""

    execution_id: UUID
    run_id: UUID
    factor_id: UUID
    factor_version_id: UUID
    factor_version_ordinal: int
    parent_factor_version_id: UUID | None
    execution_snapshot_id: UUID
    structured_strategy_version_id: UUID
    qualification: Literal["identity_metadata_not_result_release"] = (
        "identity_metadata_not_result_release"
    )


class StructuredExecutionRecords(BaseModel):
    data: tuple[StructuredExecutionRecord, ...]


def require_read_capability(session):
    from backend.app.structured_process_services import require_structured_capability
    from packages.research_runtime.process_contracts import ProcessError

    try:
        require_structured_capability(session)
    except (ProcessError, ValueError):
        raise DomainError(
            "structured_history_schema_unavailable",
            "Structured history schema is unavailable",
            409,
        ) from None


def record_projection(session, owner_id, execution):
    from sqlalchemy import text

    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.factor_models import Factor, FactorVersion
    from backend.app.structured_result_release import inconsistent, owned_execution

    owned_execution(session, owner_id, execution.run_id, execution.id)
    session.execute(
        text("SELECT mineagent_structured_outcome_check(:id)"), {"id": execution.id}
    )
    version = session.get(FactorVersion, execution.factor_version_id)
    publication = session.get(ExecutionSnapshotPublication, execution.snapshot_id)
    factor = session.get(Factor, version.factor_id) if version else None
    if (
        version is None
        or factor is None
        or publication is None
        or version.owner_id != owner_id
        or factor.owner_id != owner_id
        or publication.owner_id != owner_id
        or publication.factor_version_id != version.id
    ):
        inconsistent()
    return StructuredExecutionRecord(
        execution_id=execution.id,
        run_id=execution.run_id,
        factor_id=factor.id,
        factor_version_id=version.id,
        factor_version_ordinal=version.ordinal,
        parent_factor_version_id=version.parent_version_id,
        execution_snapshot_id=execution.snapshot_id,
        structured_strategy_version_id=execution.structured_strategy_version_id,
    )


@router.get(
    "/factors/{factor_id}/versions/{version_id}/structured-executions",
    response_model=StructuredExecutionRecords,
)
def factor_execution_records(
    factor_id: UUID,
    version_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=100000),
):
    from sqlalchemy import select

    from backend.app.factor_models import Factor, FactorVersion
    from backend.app.factor_services import owned
    from backend.app.models import ResearchRun
    from backend.app.structured_factor_models import (
        StructuredExecution,
        StructuredExecutionOutcome,
    )

    factor = owned(session, Factor, user.id, factor_id)
    version = owned(session, FactorVersion, user.id, version_id)
    if version.factor_id != factor.id:
        raise DomainError("not_found", "Factor version not found", 404)
    require_read_capability(session)
    rows = session.scalars(
        select(StructuredExecution)
        .join(
            StructuredExecutionOutcome,
            StructuredExecutionOutcome.execution_id == StructuredExecution.id,
        )
        .join(ResearchRun, ResearchRun.id == StructuredExecution.run_id)
        .where(
            StructuredExecution.owner_id == user.id,
            StructuredExecution.factor_version_id == version_id,
            ResearchRun.owner_id == user.id,
            ResearchRun.status.in_(("succeeded", "failed", "cancelled", "interrupted")),
        )
        .order_by(StructuredExecution.created_at.desc(), StructuredExecution.id.desc())
        .offset(offset)
        .limit(50)
    ).all()
    return StructuredExecutionRecords(
        data=tuple(record_projection(session, user.id, row) for row in rows)
    )


@router.get(
    "/engine-runs/{run_id}/structured-execution-records",
    response_model=StructuredExecutionRecords,
)
def run_execution_records(run_id: UUID, user: CurrentUser, session: SessionDep):
    from sqlalchemy import select

    from backend.app.models import ResearchRun
    from backend.app.structured_factor_models import (
        StructuredExecution,
        StructuredExecutionOutcome,
    )
    from backend.app.structured_factor_services import decode_structured_spec

    run = session.scalar(select(ResearchRun).filter_by(id=run_id, owner_id=user.id))
    if run is None:
        raise DomainError("not_found", "Structured run not found", 404)
    require_read_capability(session)
    decode_structured_spec(session, run)
    if run.status not in {"succeeded", "failed", "cancelled", "interrupted"}:
        raise DomainError("structured_result_pending", "Run is not terminal", 409)
    rows = session.scalars(
        select(StructuredExecution)
        .join(
            StructuredExecutionOutcome,
            StructuredExecutionOutcome.execution_id == StructuredExecution.id,
        )
        .where(
            StructuredExecution.run_id == run_id,
            StructuredExecution.owner_id == user.id,
        )
        .order_by(StructuredExecution.ordinal)
        .limit(3)
    ).all()
    if len(rows) > 2:
        raise DomainError(
            "structured_result_inconsistent",
            "Structured execution inventory exceeds its bound",
            409,
        )
    return StructuredExecutionRecords(
        data=tuple(record_projection(session, user.id, row) for row in rows)
    )


class StructuredReadCapability(BaseModel):
    can_read: bool
    can_create: Literal[False] = False
    reason_code: str


@router.get(
    "/structured-executions/capabilities", response_model=StructuredReadCapability
)
def read_capability(user: CurrentUser, session: SessionDep):
    from backend.app.structured_process_services import require_structured_capability
    from packages.research_runtime.process_contracts import ProcessError

    try:
        require_structured_capability(session)
    except (ProcessError, ValueError):
        return StructuredReadCapability(
            can_read=False, reason_code="structured_history_schema_unavailable"
        )
    return StructuredReadCapability(
        can_read=True, reason_code="retained_history_only_no_public_execution"
    )
