"""Exact normal027 execution disclosure; independent of025 different-factor pairs."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.oe_structured_identity import fail
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
)
from backend.app.oe_structured_result_release import disclose
from backend.app.oe_structured_result_schemas import (
    NormalExecutionRecord,
    NormalExecutionRelease,
    NormalExecutionView,
    NormalPairComparison,
)
from backend.app.oe_structured_result_views import (
    compare_released,
    list_records,
    project,
)
from backend.app.services import DomainError

router = APIRouter(prefix="/engine-runs/structured-search/runs")


def access(request, user, run_id, ids, *, release=False):
    return disclose(
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        run_id=run_id,
        execution_ids=ids,
        create_release=release,
    )


@router.get(
    "/{run_id}/executions",
    response_model=tuple[NormalExecutionRecord, ...],
    operation_id="list_oe_structured_executions",
)
def executions(run_id: UUID, user: CurrentUser, session: SessionDep):
    return list_records(session, user.id, run_id)


@router.post(
    "/{run_id}/executions/{execution_id}/release",
    response_model=NormalExecutionRelease,
    operation_id="release_oe_structured_execution",
)
def release_execution(
    run_id: UUID, execution_id: UUID, user: CurrentUser, request: Request
):
    _, ids = access(request, user, run_id, (execution_id,), release=True)
    return NormalExecutionRelease(
        run_id=run_id, execution_id=execution_id, exposure_ids=ids
    )


@router.get(
    "/{run_id}/executions/{execution_id}",
    response_model=NormalExecutionView,
    operation_id="read_oe_structured_execution",
)
def read_execution(
    run_id: UUID, execution_id: UUID, user: CurrentUser, request: Request
):
    values, _ = access(request, user, run_id, (execution_id,))
    return project(values[0])


@router.get(
    "/{run_id}/comparison",
    response_model=NormalPairComparison,
    operation_id="compare_oe_structured_executions",
)
def comparison(
    run_id: UUID,
    before: UUID,
    after: UUID,
    user: CurrentUser,
    request: Request,
    session: SessionDep,
):
    # Exact parent-child relation, not arbitrary best-of-history comparison.
    rows = session.scalars(
        select(OEStructuredExecution)
        .filter_by(owner_id=user.id, run_id=run_id)
        .where(OEStructuredExecution.id.in_((before, after)))
        .limit(3)
    ).all()
    indexed = {row.id: row for row in rows}
    if len(indexed) != 2 or set(indexed) != {before, after}:
        raise DomainError("not_found", "Owned normal execution pair not found", 404)
    child = session.get(OEStructuredCandidate, indexed[after].candidate_id)
    if child is None or (child.owner_id, child.run_id, child.parent_candidate_id) != (
        user.id,
        run_id,
        indexed[before].candidate_id,
    ):
        fail()
    values, _ = access(request, user, run_id, (before, after))
    return compare_released(*values)
