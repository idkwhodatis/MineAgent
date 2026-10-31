from packages.research_workflow.oe_history import (
    StructuredWorkflowView,
    WorkflowOEStageHistory,
)

"""Owner HTTP boundary for actual finite workflow services; unregistered until028.

No public owner/runtime/image declarations; no workflow creation is a claim that
an engine child has started or that guidance was consumed. Reads retain existing
source integrity and release boundaries.
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from backend.app import rdagent_steer_services as steer_service
from backend.app.api.deps import CurrentUser
from backend.app.core.maintenance import shared_lifecycle
from backend.app.rdagent_steer_models import RDSteer, RDSteerVersion
from backend.app.research_workflow_admission import select_workflow_inputs
from backend.app.research_workflow_control import request_control, require_schema
from backend.app.research_workflow_history import (
    read_selected_inputs,
    read_stage_history,
    read_workflow,
)
from backend.app.research_workflow_public import prepare_workflow, submit_workflow
from backend.app.research_workflow_public_schemas import (
    WorkflowPreflight,
    WorkflowRequest,
    WorkflowSubmit,
)
from backend.app.research_workflow_stage_list import WorkflowStageList, read_stage_list

# Separate explicit grammar: legacy RD-only URLs/bodies retain their semantics.
from backend.app.research_workflow_structured_public import (
    prepare_structured_workflow,
    submit_structured_workflow,
)
from backend.app.research_workflow_structured_schemas import (
    StructuredWorkflowPreflight,
    StructuredWorkflowRequest,
    StructuredWorkflowSubmit,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.steer import SteerVersion
from packages.research_inputs.contracts import InputVersionRef
from packages.research_workflow.admission import SelectWorkflowInputs, WorkflowCreated
from packages.research_workflow.contracts import (
    WorkflowCommand,
    WorkflowCommandReceipt,
    WorkflowInputSnapshot,
    WorkflowStageHistory,
    WorkflowView,
)
from packages.strategy_sdk import JsonLimits, parse_json

router = APIRouter()


def _body_schema(model):
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {
                key: (
                    {"propertyName": item["propertyName"]}
                    if key == "discriminator"
                    else expand(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": expand(schema)}},
        }
    }


async def _body(request, model, maximum):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > maximum:
            raise DomainError(
                "workflow_request_too_large", "Workflow request exceeds its bound", 413
            )
        raw.extend(chunk)
    try:
        parsed = parse_json(
            bytes(raw),
            limits=JsonLimits(
                max_bytes=maximum,
                max_depth=32,
                max_nodes=8192,
                max_items=4096,
                max_string=16384,
            ),
        )
        return model.model_validate_json(canonical_bytes(parsed))
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_workflow_request", "Exact bounded workflow request required", 422
        ) from None


def _transaction(request, owner, action, *, write):
    factory, settings = request.app.state.session_factory, request.app.state.settings
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        value = action(session, owner, settings)
        # Detached immutable response is formed before commit, but withheld until
        # the owned transaction and lifecycle guard have exited successfully.
        frozen = type(value).model_validate_json(canonical_bytes(value))
        if write:
            session.commit()
    return frozen


class WorkflowAvailability(BaseModel):
    can_read: bool
    preflight_available: bool
    structured_preflight_available: bool = False
    reason_code: str


@router.get("/research-workflows/capabilities", response_model=WorkflowAvailability)
async def availability(request: Request, user: CurrentUser):
    def inspect(session, owner, settings):
        try:
            require_schema(session)
        except DomainError as exc:
            if exc.code != "workflow_unavailable":
                raise
            return WorkflowAvailability(
                can_read=False,
                preflight_available=False,
                reason_code="workflow_unavailable",
            )
        return WorkflowAvailability(
            can_read=True,
            preflight_available=True,
            structured_preflight_available=(
                session.scalars(text("SELECT version_num FROM alembic_version")).all()
                in (
                    ["031_workflow_structured_stage"],
                    ["032_normal_oe_note_pins"],
                    ["032a_rd_terminal_results"],
                    ["032b_oe_append_run_state"],
                )
            ),
            reason_code="per_request_runtime_preflight_required",
        )

    return await run_in_threadpool(_transaction, request, user.id, inspect, write=False)


@router.post(
    "/research-workflows/preflight",
    response_model=WorkflowPreflight,
    openapi_extra=_body_schema(WorkflowRequest),
)
async def preflight_workflow(request: Request, user: CurrentUser):
    body = await _body(request, WorkflowRequest, 131072)
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: (
            prepare_workflow(session, owner, body, settings).preflight
        ),
        write=False,
    )


@router.post(
    "/research-workflows",
    response_model=WorkflowCreated,
    status_code=201,
    openapi_extra=_body_schema(WorkflowSubmit),
)
async def create_workflow(request: Request, user: CurrentUser):
    body = await _body(request, WorkflowSubmit, 135168)
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: submit_workflow(
            session, owner, body, settings
        ),
        write=True,
    )


@router.get(
    "/research-workflows/{workflow_id}",
    response_model=WorkflowView | StructuredWorkflowView,
)
async def workflow_detail(workflow_id: UUID, request: Request, user: CurrentUser):
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: read_workflow(
            session, owner, workflow_id, settings
        ),
        write=False,
    )


@router.get(
    "/research-workflows/{workflow_id}/stages/{stage_id}",
    response_model=WorkflowStageHistory | WorkflowOEStageHistory,
)
async def workflow_stage(
    workflow_id: UUID, stage_id: UUID, request: Request, user: CurrentUser
):
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: read_stage_history(
            session, owner, workflow_id, stage_id, settings
        ),
        write=False,
    )


@router.post(
    "/research-workflows/{workflow_id}/commands",
    response_model=WorkflowCommandReceipt,
    openapi_extra=_body_schema(WorkflowCommand),
)
async def workflow_command(workflow_id: UUID, request: Request, user: CurrentUser):
    body = await _body(request, WorkflowCommand, 4096)
    if body.workflow_id != workflow_id:
        raise DomainError(
            "invalid_workflow_request", "Workflow command scope differs", 422
        )
    if body.action == "resume":
        from backend.app.research_workflow_resume import resume_workflow

        return await run_in_threadpool(
            resume_workflow,
            request.app.state.session_factory,
            request.app.state.settings,
            owner_id=user.id,
            command=body,
        )
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: request_control(session, owner, body),
        write=True,
    )


@router.get(
    "/research-workflows/{workflow_id}/selected-inputs",
    response_model=WorkflowInputSnapshot,
)
async def selected_workflow_inputs(
    workflow_id: UUID, request: Request, user: CurrentUser
):
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: read_selected_inputs(
            session, owner, workflow_id
        ),
        write=False,
    )


@router.post(
    "/research-workflows/{workflow_id}/selected-inputs",
    response_model=WorkflowCommandReceipt,
    openapi_extra=_body_schema(SelectWorkflowInputs),
)
async def select_inputs(workflow_id: UUID, request: Request, user: CurrentUser):
    body = await _body(request, SelectWorkflowInputs, 16384)
    if body.workflow_id != workflow_id:
        raise DomainError(
            "invalid_workflow_request", "Workflow selection scope differs", 422
        )
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: select_workflow_inputs(
            session, owner, body, settings
        ),
        write=True,
    )


class WorkflowManualOption(BaseModel):
    ref: InputVersionRef
    version: SteerVersion
    disabled: bool
    revision: int


class WorkflowManualOptions(BaseModel):
    items: tuple[WorkflowManualOption, ...]
    offset: int
    has_more: bool


def _manual_options(session, owner, offset, limit):
    steer_service.require_schema(session)
    roots = session.scalars(
        select(RDSteer)
        .where(RDSteer.owner_id == owner)
        .order_by(RDSteer.created_at, RDSteer.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    items = []
    for root in roots[:limit]:
        version = steer_service.decode_version(
            session.get(RDSteerVersion, root.current_version_id), owner
        )
        if version.steer_id != root.id or version.version_id != root.current_version_id:
            raise DomainError("steer_identity_changed", "Steer identity differs", 409)
        items.append(
            WorkflowManualOption(
                ref=InputVersionRef(
                    origin="manual_user_submission",
                    record_id=root.id,
                    version_id=version.version_id,
                    content_sha256=version.sha256,
                ),
                version=version,
                disabled=root.disabled,
                revision=root.revision,
            )
        )
    return WorkflowManualOptions(
        items=tuple(items), offset=offset, has_more=len(roots) > limit
    )


@router.get("/research-workflow-inputs/manual", response_model=WorkflowManualOptions)
async def manual_options(
    request: Request,
    user: CurrentUser,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(25, ge=1, le=100),
):
    # Verified metadata only: selectable now does not mean selected, consumed,
    # applied, or approved. The actual selection service rechecks eligibility.
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: _manual_options(session, owner, offset, limit),
        write=False,
    )


@router.get(
    "/research-workflows/{workflow_id}/stages", response_model=WorkflowStageList
)
async def workflow_stages(
    workflow_id: UUID,
    request: Request,
    user: CurrentUser,
    after_ordinal: int = Query(default=0, ge=0, le=256),
    limit: int = Query(default=32, ge=1, le=64),
):
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: read_stage_list(
            session, owner, workflow_id, after_ordinal=after_ordinal, limit=limit
        ),
        write=False,
    )


@router.post(
    "/research-workflows/structured/preflight",
    response_model=StructuredWorkflowPreflight,
    openapi_extra=_body_schema(StructuredWorkflowRequest),
)
async def preflight_structured_workflow(request: Request, user: CurrentUser):
    body = await _body(request, StructuredWorkflowRequest, 131072)
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: (
            prepare_structured_workflow(session, owner, body, settings).preflight
        ),
        write=False,
    )


@router.post(
    "/research-workflows/structured",
    response_model=WorkflowCreated,
    status_code=201,
    openapi_extra=_body_schema(StructuredWorkflowSubmit),
)
async def create_structured_workflow(request: Request, user: CurrentUser):
    body = await _body(request, StructuredWorkflowSubmit, 135168)
    return await run_in_threadpool(
        _transaction,
        request,
        user.id,
        lambda session, owner, settings: submit_structured_workflow(
            session, owner, body, settings
        ),
        write=True,
    )
