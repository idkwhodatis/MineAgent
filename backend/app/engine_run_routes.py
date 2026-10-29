"""Owner-scoped engine metadata and explicitly gated configured submission/control."""

from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.engine_run_catalog import runtime_catalog
from backend.app.engine_run_schemas import (
    EngineCapabilities,
    EngineHistoryItem,
    EngineRunDetail,
    EngineRunSummary,
)
from backend.app.engine_run_submission_schemas import (
    EngineDatasetOption,
    RDControlReceipt,
    RDPreflight,
    RDSubmissionReceipt,
    RDSubmissionRequest,
    RDSubmit,
)
from backend.app.engine_run_views import (
    detail,
    history,
    list_history_runs,
    owned_engine_run,
)
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderPreflight,
    RDProviderSeedReceipt,
    RDProviderSeedRequest,
    RDProviderSeedSubmit,
    RDProviderSubmissionReceipt,
    RDProviderSubmissionRequest,
    RDProviderSubmit,
)
from backend.app.rdagent_seed_contracts import (
    RDSeededPreflight,
    RDSeededSubmissionReceipt,
    RDSeededSubmissionRequest,
    RDSeededSubmit,
)
from backend.app.schemas import Page
from backend.app.services import DomainError

router = APIRouter()


@router.get("/engine-runs/capabilities", response_model=EngineCapabilities)
def capabilities(user: CurrentUser, session: SessionDep, request: Request):
    from sqlalchemy import text

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    return runtime_catalog(
        request.app.state.settings,
        revisions[0] if len(revisions) == 1 else None,
        owner_id=user.id,
    )


@router.post(
    "/engine-runs",
    status_code=201,
    response_model=RDSubmissionReceipt
    | RDSeededSubmissionReceipt
    | RDProviderSubmissionReceipt
    | RDProviderSeedReceipt,
)
async def create_configured_submission(
    body: RDSubmit | RDSeededSubmit | RDProviderSubmit | RDProviderSeedSubmit,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    from starlette.concurrency import run_in_threadpool

    from backend.app.rdagent_submission_services import submit_configured_run
    from packages.strategy_sdk import JsonLimits, parse_json

    try:
        parsed = parse_json(
            await request.body(),
            limits=JsonLimits(max_bytes=131072, max_nodes=10000, max_items=256),
        )
        from backend.app.rdagent_seed_http import parse_submission

        exact = parse_submission(parsed, submitted=True)
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_engine_request",
            "Bounded duplicate-free RD submission required",
            422,
        ) from None
    if exact != body:
        raise DomainError(
            "invalid_engine_request", "RD request normalization differs", 422
        )

    def persist():
        if isinstance(exact, (RDProviderSubmit, RDProviderSeedSubmit)):
            from backend.app.rdagent_provider_submission import submit_provider_run

            writer = submit_provider_run
        elif isinstance(exact, RDSeededSubmit):
            from backend.app.rdagent_seed_submission import submit_seeded_run

            writer = submit_seeded_run
        else:
            writer = submit_configured_run
        result = writer(
            session, user.id, exact, idempotency_key, request.app.state.settings
        )
        session.commit()
        return result

    return await run_in_threadpool(persist)


@router.get("/engine-runs", response_model=Page[EngineRunSummary])
def list_runs(
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    return list_history_runs(session, user.id, offset, limit)


@router.get("/engine-runs/datasets", response_model=Page[EngineDatasetOption])
def eligible_datasets(
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    from sqlalchemy import func, select

    from backend.app.engine_run_preflight import dataset_options
    from backend.app.models import DatasetVersion

    rows = dataset_options(session, user.id, offset, limit)
    count = session.scalar(
        select(func.count())
        .select_from(DatasetVersion)
        .where(DatasetVersion.owner_id == user.id)
    )
    return {"data": rows, "count": count}


@router.post(
    "/engine-runs/preflight",
    response_model=RDPreflight | RDSeededPreflight | RDProviderPreflight,
)
async def preflight_submission(
    body: RDSubmissionRequest
    | RDSeededSubmissionRequest
    | RDProviderSubmissionRequest
    | RDProviderSeedRequest,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    from starlette.concurrency import run_in_threadpool

    from backend.app.engine_run_preflight import preflight
    from packages.strategy_sdk import JsonLimits, parse_json

    try:
        parsed = parse_json(
            await request.body(),
            limits=JsonLimits(max_bytes=131072, max_nodes=10000, max_items=256),
        )
        from backend.app.rdagent_seed_http import parse_submission

        exact = parse_submission(parsed, submitted=False)
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_engine_request", "Bounded duplicate-free RD request required", 422
        ) from None
    if exact != body:
        raise DomainError(
            "invalid_engine_request", "RD request normalization differs", 422
        )
    if isinstance(exact, (RDProviderSubmissionRequest, RDProviderSeedRequest)):
        from backend.app.rdagent_provider_preflight import provider_preflight

        reader = provider_preflight
    elif isinstance(exact, RDSeededSubmissionRequest):
        from backend.app.rdagent_seed_preflight import seeded_preflight

        reader = seeded_preflight
    else:
        reader = preflight
    return await run_in_threadpool(
        reader, session, user.id, exact, request.app.state.settings
    )


@router.get("/engine-runs/{run_id}", response_model=EngineRunDetail)
def read_run(run_id: UUID, user: CurrentUser, session: SessionDep):
    from backend.app.rdagent_control_services import controls_for

    run = owned_engine_run(session, run_id, user.id)
    return detail(run, session=session).model_copy(
        update={"actions": controls_for(session, run)}
    )


@router.get("/engine-runs/{run_id}/history", response_model=Page[EngineHistoryItem])
def read_history(
    run_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    return history(session, owned_engine_run(session, run_id, user.id), offset, limit)


@router.post("/engine-runs/{run_id}/cancel", response_model=RDControlReceipt)
def cancel_configured_run(run_id: UUID, user: CurrentUser, session: SessionDep):
    from backend.app.rdagent_control_services import request_cancel

    result = request_cancel(session, user.id, run_id)
    session.commit()
    return result
