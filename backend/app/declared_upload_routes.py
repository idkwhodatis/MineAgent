"""Authenticated explicit composition, never implicit model execution or release."""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.maintenance import shared_lifecycle
from backend.app.declared_upload_schemas import (
    DeclaredUploadBasketCreate,
    DeclaredUploadBasketReceipt,
)
from backend.app.declared_upload_services import (
    PUBLICATION_PEAK_BYTES,
    PUBLICATION_PEAK_FILES,
    publish_basket,
)
from backend.app.factor_services import publication
from backend.app.research_workflow_routes import _body, _body_schema
from backend.app.schemas import DatasetPublic

router = APIRouter()


@router.post(
    "/data/declared-upload-baskets",
    operation_id="create_declared_upload_basket",
    response_model=DeclaredUploadBasketReceipt,
    openapi_extra=_body_schema(DeclaredUploadBasketCreate),
)
async def create_declared_upload_basket(
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    exact = await _body(request, DeclaredUploadBasketCreate, 4096)
    owner_id = user.id
    settings = request.app.state.settings

    def persist():
        with (
            shared_lifecycle(session.get_bind(), settings.storage_root),
            publication(
                session,
                owner_id,
                settings,
                peak_bytes=PUBLICATION_PEAK_BYTES,
                peak_files=PUBLICATION_PEAK_FILES,
                include_dataset_inventory=True,
            ) as admission,
        ):
            row = publish_basket(
                session, owner_id, exact, settings, admission=admission
            )
            receipt = DeclaredUploadBasketReceipt(
                dataset=DatasetPublic.model_validate(row)
            )
        return receipt

    return await run_in_threadpool(persist)
