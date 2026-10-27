"""Explicit owner-authorized disclosure of retained terminal historical results."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from backend.app.api.deps import CurrentUser
from backend.app.engine_result_release import disclose
from backend.app.services import DomainError
from packages.factors.contracts import FeatureRow, canonical_bytes
from packages.qlib_adapter.contracts import DirectResult
from packages.qlib_adapter.objective import DirectObjectiveMeasurement

router = APIRouter()
QUALIFICATION = "owned_historical_integrity_not_live_execution_authority"


class EngineResultRelease(BaseModel):
    run_id: UUID
    resource_id: UUID
    resource_kind: Literal["execution_snapshot", "direct_analysis"]
    exposure_ids: tuple[UUID, ...]
    extent: Literal["conservative_full_retained_dataset"] = (
        "conservative_full_retained_dataset"
    )
    qualification: Literal["exposure_record_not_execution_authority"] = (
        "exposure_record_not_execution_authority"
    )


class ExecutionSnapshotValue(FeatureRow):
    """Dedicated execution row schema, distinct from legacy replay FeatureRow."""


class ExecutionSnapshotValues(BaseModel):
    execution_snapshot_id: UUID
    run_id: UUID
    manifest_sha256: str
    input_sha256: str
    output_sha256: str
    data: tuple[ExecutionSnapshotValue, ...]
    count: int
    offset: int
    exposure_ids: tuple[UUID, ...]
    qualification: Literal[
        "owned_historical_integrity_not_live_execution_authority"
    ] = QUALIFICATION


class DirectAnalysisResult(BaseModel):
    analysis_id: UUID
    execution_snapshot_id: UUID
    run_id: UUID
    result_sha256: str
    measurement_sha256: str
    result: DirectResult
    measurement: DirectObjectiveMeasurement
    exposure_ids: tuple[UUID, ...]
    qualification: Literal[
        "owned_historical_integrity_not_live_execution_authority"
    ] = QUALIFICATION


def access(request, user, run_id, resource_id, kind, *, release):
    return disclose(
        request.app.state.session_factory,
        request.app.state.settings,
        owner_id=user.id,
        run_id=run_id,
        resource_id=resource_id,
        kind=kind,
        create_release=release,
    )


@router.post(
    "/engine-runs/{run_id}/execution-snapshots/{snapshot_id}/release",
    response_model=EngineResultRelease,
)
def release_snapshot(
    run_id: UUID, snapshot_id: UUID, user: CurrentUser, request: Request
):
    _, exposure_ids = access(
        request, user, run_id, snapshot_id, "execution_snapshot", release=True
    )
    return EngineResultRelease(
        run_id=run_id,
        resource_id=snapshot_id,
        resource_kind="execution_snapshot",
        exposure_ids=exposure_ids,
    )


@router.get(
    "/engine-runs/{run_id}/execution-snapshots/{snapshot_id}/values",
    response_model=ExecutionSnapshotValues,
)
def snapshot_values(
    run_id: UUID,
    snapshot_id: UUID,
    user: CurrentUser,
    request: Request,
    offset: int = Query(0, ge=0, le=10000000),
    limit: int = Query(100, ge=1, le=1000),
):
    result, exposure_ids = access(
        request, user, run_id, snapshot_id, "execution_snapshot", release=False
    )
    return ExecutionSnapshotValues(
        execution_snapshot_id=snapshot_id,
        run_id=run_id,
        manifest_sha256=result.manifest_sha256,
        input_sha256=result.input_sha256,
        output_sha256=result.output_sha256,
        data=tuple(
            ExecutionSnapshotValue.model_validate(row.model_dump())
            for row in result.rows[offset : offset + limit]
        ),
        count=len(result.rows),
        offset=offset,
        exposure_ids=exposure_ids,
    )


@router.post(
    "/engine-runs/{run_id}/analyses/{analysis_id}/release",
    response_model=EngineResultRelease,
)
def release_analysis(
    run_id: UUID, analysis_id: UUID, user: CurrentUser, request: Request
):
    _, exposure_ids = access(
        request, user, run_id, analysis_id, "direct_analysis", release=True
    )
    return EngineResultRelease(
        run_id=run_id,
        resource_id=analysis_id,
        resource_kind="direct_analysis",
        exposure_ids=exposure_ids,
    )


@router.get(
    "/engine-runs/{run_id}/analyses/{analysis_id}", response_model=DirectAnalysisResult
)
def read_analysis(run_id: UUID, analysis_id: UUID, user: CurrentUser, request: Request):
    result, exposure_ids = access(
        request, user, run_id, analysis_id, "direct_analysis", release=False
    )
    return DirectAnalysisResult(
        analysis_id=analysis_id,
        execution_snapshot_id=result.snapshot_id,
        run_id=run_id,
        result_sha256=result.result_sha256,
        measurement_sha256=result.measurement_sha256,
        result=result.result,
        measurement=result.measurement,
        exposure_ids=exposure_ids,
    )


@router.get("/engine-runs/{run_id}/execution-snapshots/{snapshot_id}/export")
def export_snapshot(
    run_id: UUID, snapshot_id: UUID, user: CurrentUser, request: Request
):
    result, exposure_ids = access(
        request, user, run_id, snapshot_id, "execution_snapshot", release=False
    )
    raw = canonical_bytes(
        {
            "schema_version": "mineagent.execution-snapshot-export.v1",
            "execution_snapshot_id": str(snapshot_id),
            "run_id": str(run_id),
            "manifest_sha256": result.manifest_sha256,
            "input_sha256": result.input_sha256,
            "output_sha256": result.output_sha256,
            "rows": [row.model_dump(mode="json") for row in result.rows],
            "exposure_ids": [str(value) for value in exposure_ids],
            "qualification": QUALIFICATION,
        }
    )
    if len(raw) > 32 * 1024**2:
        raise DomainError(
            "engine_export_too_large",
            "Historical export exceeds bounded response size",
            409,
        )
    return Response(
        raw,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="execution-snapshot-{snapshot_id}.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
