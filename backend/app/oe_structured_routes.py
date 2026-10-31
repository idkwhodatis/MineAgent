"""Normal authenticated027 HTTP adapter; register only with coherent027 worker/schema.

Imports alone do not add this router to the app. Provider URLs/secrets and runtime
pins never come from these request bodies. Existing bearer auth is unchanged.
"""

from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from starlette.concurrency import run_in_threadpool

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.oe_structured_catalog import list_snapshots
from backend.app.oe_structured_catalog_schemas import (
    StructuredModelOption,
    StructuredSearchCapabilities,
    StructuredSnapshotPage,
)
from backend.app.oe_structured_preflight import preflight
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredPreflight,
    ProviderStructuredSearchRequest,
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from backend.app.oe_structured_run_schemas import (
    StructuredControlReceipt,
    WorkflowStructuredRunView,
)
from backend.app.oe_structured_run_views import (
    ProviderStructuredRunView,
    read_owned_run,
)
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.oe_structured_submission_services import submit
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.candidate import _raw_fields
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
from packages.strategy_sdk import JsonLimits, parse_json

router = APIRouter()


@router.get(
    "/engine-runs/structured-search/capabilities",
    operation_id="get_oe_structured_capabilities",
    response_model=StructuredSearchCapabilities,
)
def capabilities(user: CurrentUser, session: SessionDep):
    from sqlalchemy import text

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    available = revisions in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )
    return StructuredSearchCapabilities(
        routes_available=available,
        can_preflight=available,
        reason_code=None if available else "runtime_schema_unavailable",
    )


async def _exact(request, body, model):
    try:
        raw = parse_json(
            await request.body(),
            limits=JsonLimits(max_bytes=65536, max_nodes=5000, max_items=256),
        )
        exact = model.model_validate_json(canonical_bytes(raw))
        if exact != body:
            raise ValueError("normalization")
        return exact
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_oe_structured_request",
            "Bounded duplicate-free structured request required",
            422,
        ) from None


@router.get(
    "/engine-runs/structured-search/model-profiles",
    operation_id="list_oe_structured_model_profiles",
    response_model=tuple[StructuredModelOption, ...],
)
async def model_profiles(user: CurrentUser, request: Request):
    owner_id = user.id
    settings = request.app.state.settings

    def read():
        values = []
        seen = set()
        for raw in getattr(settings, "oe_structured_provider_profiles", ()):
            try:
                profile = OEOperatorChatProfile.model_validate(_raw_fields(raw))
                binding = profile.public_binding(owner_id)
            except (ValueError, TypeError):
                continue
            if binding.profile_id in seen:
                # Duplicate config is not resolved arbitrarily into authority.
                return ()
            seen.add(binding.profile_id)
            available = True
            reason = None
            try:
                resolve_runtime(settings)
                resolve_operator_profile(settings, owner_id, binding.profile_id)
            except DomainError as error:
                available = False
                reason = error.code
            values.append(
                StructuredModelOption(
                    binding=binding,
                    binding_sha256=binding.sha256,
                    label=profile.connection.label,
                    operator_available=available,
                    unavailable_reason=reason,
                )
            )
        return tuple(sorted(values, key=lambda item: item.binding.profile_id))

    return await run_in_threadpool(read)


@router.post(
    "/engine-runs/structured-search/preflight",
    operation_id="preflight_oe_structured_search",
    response_model=ProviderStructuredPreflight,
)
async def check_search(
    body: ProviderStructuredSearchRequest, user: CurrentUser, request: Request
):
    exact = await _exact(request, body, ProviderStructuredSearchRequest)
    return await run_in_threadpool(
        preflight,
        request.app.state.session_factory,
        user.id,
        exact,
        request.app.state.settings,
    )


@router.post(
    "/engine-runs/structured-search",
    operation_id="submit_oe_structured_search",
    status_code=201,
    response_model=ProviderStructuredSubmissionReceipt,
)
async def create_search(
    body: ProviderStructuredSearchSubmit,
    user: CurrentUser,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    exact = await _exact(request, body, ProviderStructuredSearchSubmit)
    # submit owns its transaction/commit; do not acknowledge a detached queued DTO.
    return await run_in_threadpool(
        submit,
        request.app.state.session_factory,
        user.id,
        exact,
        idempotency_key,
        request.app.state.settings,
    )


@router.get(
    "/engine-runs/structured-search/snapshots",
    operation_id="list_oe_structured_snapshots",
    response_model=StructuredSnapshotPage,
)
def snapshots(
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
):
    return list_snapshots(session, user.id, offset=offset, limit=limit)


@router.get(
    "/engine-runs/structured-search/runs/{run_id}",
    operation_id="get_oe_structured_run",
    response_model=ProviderStructuredRunView | WorkflowStructuredRunView,
)
def get_structured_run(run_id: UUID, user: CurrentUser, session: SessionDep):
    return read_owned_run(session, user.id, run_id)


@router.post(
    "/engine-runs/structured-search/runs/{run_id}/cancel",
    response_model=StructuredControlReceipt,
    operation_id="cancel_oe_structured_run",
)
def cancel_structured_run(run_id: UUID, user: CurrentUser, request: Request):
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.oe_structured_control_services import request_cancel

    factory = request.app.state.session_factory
    with (
        shared_lifecycle(factory.kw["bind"], request.app.state.settings.storage_root),
        factory.begin() as session,
    ):
        receipt = request_cancel(session, user.id, run_id)
    return receipt
