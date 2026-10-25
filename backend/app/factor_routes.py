"""Authenticated independent factor library. No strategy stand-ins or unsafe import."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from backend.app import factor_import_services as imports
from backend.app import factor_services as service
from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.factor_models import Factor, FactorVersion
from backend.app.factor_schemas import (
    FactorBundleCreate,
    FactorComparisonCreate,
    FactorCreate,
    FactorExportCreate,
    FactorPatch,
    FactorVersionCreate,
)
from backend.app.services import DomainError

router = APIRouter()
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("/factors/capabilities", operation_id="factor_capabilities")
def capabilities(user: CurrentUser, request: Request):
    return {
        "enabled": request.app.state.settings.factor_library_enabled,
        "package_import_enabled": imports.import_available(request.app.state.settings),
    }


@router.get("/factors", operation_id="list_factors")
def factors(
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=240),
    bookmarked: bool | None = None,
    kind: Literal["cross_sectional", "time_series"] | None = None,
):
    service.enabled(request.app.state.settings)
    return service.list_factors(
        session,
        user.id,
        offset=offset,
        limit=limit,
        search=search,
        bookmarked=bookmarked,
        kind=kind,
    )


@router.post("/factors", operation_id="create_factor", status_code=201)
def create_factor(
    body: FactorCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.create_factor(
        session, user.id, body, idempotency_key, request.app.state.settings
    )


@router.get("/factors/{factor_id}", operation_id="get_factor")
def get_factor(
    factor_id: UUID, user: CurrentUser, session: SessionDep, request: Request
):
    service.enabled(request.app.state.settings)
    return service.public(service.owned(session, Factor, user.id, factor_id))


@router.patch("/factors/{factor_id}", operation_id="patch_factor")
def patch_factor(
    factor_id: UUID,
    body: FactorPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    service.enabled(request.app.state.settings)
    return service.patch_factor(session, user.id, factor_id, body)


@router.get("/factors/{factor_id}/versions", operation_id="list_factor_versions")
def versions(
    factor_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=50, ge=1, le=100),
):
    service.enabled(request.app.state.settings)
    service.owned(session, Factor, user.id, factor_id)
    query = select(FactorVersion).where(
        FactorVersion.owner_id == user.id, FactorVersion.factor_id == factor_id
    )
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "data": [
            service.public(row)
            for row in session.scalars(
                query.order_by(FactorVersion.ordinal.desc()).offset(offset).limit(limit)
            )
        ],
        "count": count,
    }


@router.post(
    "/factors/{factor_id}/versions",
    operation_id="create_factor_version",
    status_code=201,
)
def create_version(
    factor_id: UUID,
    body: FactorVersionCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.create_factor(
        session, user.id, body, idempotency_key, request.app.state.settings, factor_id
    )


@router.get(
    "/factors/{factor_id}/versions/{version_id}", operation_id="get_factor_version"
)
def version(
    factor_id: UUID,
    version_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    service.enabled(request.app.state.settings)
    return service.get_version(
        session, user.id, factor_id, version_id, request.app.state.settings
    )


@router.get("/factor-snapshots/{snapshot_id}", operation_id="get_factor_snapshot")
def snapshot(
    snapshot_id: UUID, user: CurrentUser, session: SessionDep, request: Request
):
    service.enabled(request.app.state.settings)
    row, _, _ = service.verified_snapshot(
        session, user.id, snapshot_id, request.app.state.settings
    )
    service.expose_snapshot(session, user.id, row, "manifest")
    result = service.public(row)
    session.commit()
    return result


@router.get(
    "/factor-snapshots/{snapshot_id}/values", operation_id="factor_snapshot_values"
)
def values(
    snapshot_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    listing_id: str | None = Query(default=None, max_length=200),
    start: date | None = None,
    end: date | None = None,
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=100, ge=1, le=500),
):
    service.enabled(request.app.state.settings)
    return service.snapshot_values(
        session,
        user.id,
        snapshot_id,
        request.app.state.settings,
        listing_id=listing_id,
        start=start,
        end=end,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/factor-snapshots/{snapshot_id}/sample", operation_id="factor_snapshot_sample"
)
def sample(
    snapshot_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    listing_id: str = Query(max_length=200),
    session_date: Annotated[date, Query()] = ...,
):
    service.enabled(request.app.state.settings)
    return service.snapshot_sample(
        session,
        user.id,
        snapshot_id,
        request.app.state.settings,
        listing_id,
        session_date,
    )


@router.post("/factor-bundles", operation_id="create_factor_bundle", status_code=201)
def bundle(
    body: FactorBundleCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.create_bundle(
        session, user.id, body, idempotency_key, request.app.state.settings
    )


@router.post(
    "/factor-comparisons", operation_id="create_factor_comparison", status_code=201
)
def comparison(
    body: FactorComparisonCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.create_comparison(
        session, user.id, body, idempotency_key, request.app.state.settings
    )


@router.get("/factor-comparisons/{comparison_id}", operation_id="get_factor_comparison")
def get_comparison(
    comparison_id: UUID, user: CurrentUser, session: SessionDep, request: Request
):
    service.enabled(request.app.state.settings)
    return service.get_comparison(
        session, user.id, comparison_id, request.app.state.settings
    )


@router.post(
    "/factor-versions/{version_id}/exports",
    operation_id="export_factor_package",
    status_code=201,
)
def export(
    version_id: UUID,
    body: FactorExportCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.export_package(
        session, user.id, version_id, body, idempotency_key, request.app.state.settings
    )


@router.get(
    "/factor-packages/{package_id}/download", operation_id="download_factor_package"
)
def download(
    package_id: UUID, user: CurrentUser, session: SessionDep, request: Request
):
    service.enabled(request.app.state.settings)
    data = service.download_package(
        session, user.id, package_id, request.app.state.settings
    )
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="factor-{package_id}.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/factor-packages/import", operation_id="import_factor_package", status_code=202
)
async def import_package(
    user: CurrentUser, session: SessionDep, request: Request, idempotency_key: Key
):
    settings = request.app.state.settings
    service.enabled(settings)
    if not imports.import_available(settings):
        raise DomainError(
            "factor_import_unavailable",
            "Isolated factor import admission is not enabled",
            503,
        )
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (
        media not in {"application/zip", "application/octet-stream"}
        or request.headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise DomainError(
            "factor_import_media_type", "Raw unencoded archive body is required", 415
        )
    owner_id = user.id
    session.rollback()
    archive = await request.body()
    return await run_in_threadpool(
        imports.admit_import, session, owner_id, archive, idempotency_key, settings
    )


@router.get("/factor-imports/{admission_id}", operation_id="get_factor_import_status")
def import_status(
    admission_id: UUID, user: CurrentUser, session: SessionDep, request: Request
):
    return imports.import_status(
        session, user.id, admission_id, request.app.state.settings
    )


@router.get(
    "/factor-comparisons/{comparison_id}/values",
    operation_id="factor_comparison_values",
)
def comparison_values(
    comparison_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=100, ge=1, le=500),
):
    service.enabled(request.app.state.settings)
    return service.comparison_values(
        session,
        user.id,
        comparison_id,
        request.app.state.settings,
        offset=offset,
        limit=limit,
    )


from backend.app.factor_schemas import FixedFixtureCreate


@router.post(
    "/factor-fixtures", operation_id="create_fixed_factor_fixture", status_code=201
)
def fixture(
    body: FixedFixtureCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Key,
):
    return service.create_fixed_fixture(
        session, user.id, body, idempotency_key, request.app.state.settings
    )
