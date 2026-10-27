"""Factor Library navigation for dedicated execution identities, never value release.

Only immutable identity/publication state is returned. No row counts, values,
metrics, objective status, storage addresses or inferred legacy snapshots.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import and_, func, select

from backend.app import factor_services
from backend.app.engine_run_views import owned_engine_run
from backend.app.engine_snapshot_routes import metadata_projection
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.factor_analysis_models import (
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.factor_models import Factor, FactorVersion
from backend.app.services import DomainError
from packages.factors.execution_snapshot import ExecutionSnapshotManifest


class FactorExecutionRecord(BaseModel):
    kind: Literal["execution_snapshot"] = "execution_snapshot"
    factor_id: UUID
    factor_version_id: UUID
    run_id: UUID
    execution_snapshot_id: UUID
    source_id: UUID
    evaluation_id: UUID
    publication_state: Literal["publication_pending", "completed"]
    analysis_id: UUID | None
    analysis_state: Literal["not_declared", "pending", "result_recorded"]
    created_at: datetime
    qualification: Literal["identity_metadata_not_result_release"] = (
        "identity_metadata_not_result_release"
    )


class FactorExecutionPage(BaseModel):
    data: list[FactorExecutionRecord]
    count: int


def project_record(run, version, publication, outcome, analysis, result):
    metadata = metadata_projection(run, publication, outcome)
    manifest = ExecutionSnapshotManifest.model_validate(publication.manifest)
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    if (publication.owner_id, publication.factor_version_id) != (
        version.owner_id,
        version.id,
    ) or (
        manifest.input.version.code_sha256,
        manifest.input.version.definition_sha256,
    ) != (version.code_sha256, version.definition_sha256):
        raise DomainError(
            "factor_execution_inconsistent", "Execution factor identity differs", 409
        )
    if analysis is not None and (
        analysis.snapshot_id != publication.id
        or outcome is None
        or any(getattr(analysis, k) != getattr(publication, k) for k in scope)
    ):
        raise DomainError(
            "factor_execution_inconsistent", "Analysis snapshot identity differs", 409
        )
    if result is not None and (
        analysis is None
        or result.analysis_id != analysis.id
        or any(getattr(result, k) != getattr(publication, k) for k in scope)
    ):
        raise DomainError(
            "factor_execution_inconsistent", "Analysis result identity differs", 409
        )
    return FactorExecutionRecord(
        factor_id=version.factor_id,
        factor_version_id=version.id,
        run_id=run.id,
        execution_snapshot_id=publication.id,
        source_id=publication.source_id,
        evaluation_id=publication.evaluation_id,
        publication_state=metadata.state,
        analysis_id=analysis.id if analysis else None,
        analysis_state="result_recorded"
        if result
        else "pending"
        if analysis
        else "not_declared",
        created_at=publication.created_at,
    )


def list_execution_records(
    session, owner_id, factor_id, version_id, *, offset=0, limit=50
):
    factor_services.owned(session, Factor, owner_id, factor_id)
    version = factor_services.owned(session, FactorVersion, owner_id, version_id)
    if version.factor_id != factor_id:
        raise DomainError("not_found", "Factor version not found", 404)
    pub, out = ExecutionSnapshotPublication, ExecutionSnapshotOutcome
    analysis, result = FactorDirectAnalysisIntent, FactorDirectAnalysisOutcome
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    query = (
        select(pub, out, analysis, result)
        .select_from(pub)
        .outerjoin(
            out,
            and_(
                out.publication_id == pub.id,
                *(getattr(out, k) == getattr(pub, k) for k in scope),
            ),
        )
        .outerjoin(
            analysis,
            and_(
                analysis.snapshot_id == pub.id,
                *(getattr(analysis, k) == getattr(pub, k) for k in scope),
            ),
        )
        .outerjoin(
            result,
            and_(
                result.analysis_id == analysis.id,
                *(getattr(result, k) == getattr(pub, k) for k in scope),
            ),
        )
        .where(pub.owner_id == owner_id, pub.factor_version_id == version_id)
    )
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(
        query.order_by(pub.created_at.desc(), pub.id.desc()).offset(offset).limit(limit)
    ).all()
    return FactorExecutionPage(
        data=[
            project_record(
                owned_engine_run(session, row[0].run_id, owner_id), version, *row
            )
            for row in rows
        ],
        count=count,
    )
