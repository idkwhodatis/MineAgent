"""Authenticated persistent steer management; no run mutation or model execution."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from backend.app import rdagent_steer_services as service
from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.rdagent_steer_models import RDSteer, RDSteerVersion
from backend.app.services import DomainError
from packages.factors.contracts import Contract
from packages.rdagent_adapter.steer import SteerKind, SteerVersion
from packages.strategy_sdk import JsonLimits, parse_json

router = APIRouter()


class SteerWrite(Contract):
    kind: SteerKind
    text: str = Field(min_length=1, max_length=2048)


class SteerEdit(SteerWrite):
    expected_revision: int = Field(strict=True, ge=1, le=1000000)


class SteerState(Contract):
    expected_revision: int = Field(strict=True, ge=1, le=1000000)
    disabled: bool = Field(strict=True)


class SteerView(BaseModel):
    id: UUID
    revision: int
    disabled: bool
    current: SteerVersion
    created_at: datetime
    updated_at: datetime


class SteerPage(BaseModel):
    data: tuple[SteerView, ...]
    count: int


class SteerHistory(BaseModel):
    steer_id: UUID
    data: tuple[SteerVersion, ...]
    count: int


class SteerCapability(BaseModel):
    can_manage: bool
    reason_code: str


def view(root, version):
    return SteerView(
        id=root.id,
        revision=root.revision,
        disabled=root.disabled,
        current=service.decode_version(version, root.owner_id),
        created_at=root.created_at,
        updated_at=root.updated_at,
    )


@router.get("/rd-steers/capabilities", response_model=SteerCapability)
def capabilities(user: CurrentUser, session: SessionDep):
    try:
        service.require_schema(session)
    except DomainError as exc:
        if exc.code != "steer_schema_unavailable":
            raise
        return SteerCapability(can_manage=False, reason_code=exc.code)
    return SteerCapability(
        can_manage=True, reason_code="future_run_guidance_not_live_edit"
    )


@router.get("/rd-steers", response_model=SteerPage)
def list_steers(
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=100),
    limit: int = Query(25, ge=1, le=100),
):
    service.require_schema(session)
    rows = session.scalars(
        select(RDSteer)
        .where(RDSteer.owner_id == user.id)
        .order_by(RDSteer.created_at, RDSteer.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return SteerPage(
        data=tuple(
            view(root, session.get(RDSteerVersion, root.current_version_id))
            for root in rows
        ),
        count=session.scalar(
            select(func.count()).select_from(RDSteer).where(RDSteer.owner_id == user.id)
        ),
    )


@router.get("/rd-steers/{steer_id}", response_model=SteerView)
def detail(steer_id: UUID, user: CurrentUser, session: SessionDep):
    service.require_schema(session)
    root = service.owned_root(session, user.id, steer_id)
    return view(root, session.get(RDSteerVersion, root.current_version_id))


@router.get("/rd-steers/{steer_id}/versions", response_model=SteerHistory)
def history(steer_id: UUID, user: CurrentUser, session: SessionDep):
    service.require_schema(session)
    service.owned_root(session, user.id, steer_id)
    rows = session.scalars(
        select(RDSteerVersion)
        .where(RDSteerVersion.owner_id == user.id, RDSteerVersion.steer_id == steer_id)
        .order_by(RDSteerVersion.ordinal)
        .limit(65)
    ).all()
    if len(rows) > 64:
        raise DomainError(
            "steer_history_inconsistent", "Steer history exceeds retained bound", 409
        )
    return SteerHistory(
        steer_id=steer_id,
        data=tuple(service.decode_version(row, user.id) for row in rows),
        count=len(rows),
    )


async def exact_body(request, body):
    try:
        parsed = parse_json(
            await request.body(),
            limits=JsonLimits(max_bytes=32768, max_nodes=64, max_items=16),
        )
        exact = type(body).model_validate(parsed)
        if exact != body:
            raise ValueError("normalization changed")
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_steer_request",
            "Bounded duplicate-free steer request required",
            422,
        ) from None


@router.post("/rd-steers", response_model=SteerView, status_code=201)
async def create(
    body: SteerWrite, user: CurrentUser, session: SessionDep, request: Request
):
    await exact_body(request, body)

    def persist():
        root, version = service.create_steer(session, user.id, body)
        result = view(root, version)
        session.commit()
        return result

    return await run_in_threadpool(persist)


@router.patch("/rd-steers/{steer_id}", response_model=SteerView)
async def edit(
    steer_id: UUID,
    body: SteerEdit,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    await exact_body(request, body)

    def persist():
        root, version = service.edit_steer(session, user.id, steer_id, body)
        result = view(root, version)
        session.commit()
        return result

    return await run_in_threadpool(persist)


@router.post("/rd-steers/{steer_id}/state", response_model=SteerView)
async def state(
    steer_id: UUID,
    body: SteerState,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    await exact_body(request, body)

    def persist():
        root, version = service.set_disabled(session, user.id, steer_id, body)
        result = view(root, version)
        session.commit()
        return result

    return await run_in_threadpool(persist)
