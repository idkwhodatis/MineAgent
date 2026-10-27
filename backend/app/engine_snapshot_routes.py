"""Read-only persisted execution snapshot metadata, not retained-byte verification.

This route never opens files, issues a process grant or exposes result values.
Restored-byte verification/release remains a separate storage authority boundary.
"""

import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, select

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.engine_run_views import owned_engine_run
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest

router = APIRouter()


class ExecutionSnapshotMetadata(BaseModel):
    id: UUID
    schema_version: Literal["mineagent.factor-execution-snapshot.v1"]
    run_id: UUID
    attempt_id: UUID
    generation: int
    generation_start_id: UUID
    source_id: UUID
    evaluation_id: UUID
    factor_version_id: UUID
    dataset_version_id: UUID
    state: Literal["publication_pending", "completed"]
    created_at: datetime
    completed_at: datetime | None
    window_start: date
    window_end: date
    expected_cutoffs: None = None
    row_count: None = None
    valid_count: None = None
    manifest_sha256: str
    input_sha256: str
    output_sha256: str | None
    dependency_claim: Literal["provided_not_proven_read"]
    qualification: Literal["persisted_metadata_not_reexecuted_or_reverified"] = (
        "persisted_metadata_not_reexecuted_or_reverified"
    )
    values_available: Literal[False] = False
    analysis_results_available: Literal[False] = False


def metadata_projection(run, publication, outcome):
    try:
        manifest = ExecutionSnapshotManifest.model_validate_json(
            json.dumps(publication.manifest, allow_nan=False)
        )
        expected = (
            publication.id,
            publication.owner_id,
            publication.run_id,
            publication.attempt_id,
            publication.generation,
            publication.start_id,
            publication.source_id,
            publication.evaluation_id,
            publication.factor_version_id,
        )
        actual = (
            manifest.snapshot_id,
            manifest.owner_id,
            manifest.run_id,
            manifest.attempt_id,
            manifest.generation,
            manifest.generation_start_id,
            manifest.source_id,
            manifest.evaluation_id,
            manifest.input.version.factor_version_id,
        )
        if (
            actual != expected
            or publication.owner_id != run.owner_id
            or publication.run_id != run.id
            or manifest.input.dataset.dataset_version_id != run.dataset_version_id
            or digest(manifest) != publication.manifest_sha256
        ):
            raise ValueError("Snapshot identity differs")
        if outcome and (
            tuple(
                getattr(outcome, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            )
            != tuple(
                getattr(publication, k)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            )
            or (
                outcome.publication_id,
                outcome.manifest_sha256,
                outcome.input_sha256,
                outcome.output_sha256,
            )
            != (
                publication.id,
                publication.manifest_sha256,
                manifest.input_sha256,
                manifest.output_rows_sha256,
            )
        ):
            raise ValueError("Snapshot outcome differs")
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "engine_snapshot_metadata_inconsistent",
            "Persisted execution snapshot metadata differs",
            409,
        ) from None
    return ExecutionSnapshotMetadata(
        id=manifest.snapshot_id,
        schema_version=manifest.schema_version,
        run_id=manifest.run_id,
        attempt_id=manifest.attempt_id,
        generation=manifest.generation,
        generation_start_id=manifest.generation_start_id,
        source_id=manifest.source_id,
        evaluation_id=manifest.evaluation_id,
        factor_version_id=manifest.input.version.factor_version_id,
        dataset_version_id=manifest.input.dataset.dataset_version_id,
        state="completed" if outcome else "publication_pending",
        created_at=publication.created_at,
        completed_at=outcome.created_at if outcome else None,
        window_start=manifest.input.window.start,
        window_end=manifest.input.window.end,
        manifest_sha256=publication.manifest_sha256,
        input_sha256=manifest.input_sha256,
        output_sha256=outcome.output_sha256 if outcome else None,
        dependency_claim=manifest.dependency_claim,
    )


@router.get(
    "/engine-runs/{run_id}/execution-snapshots/{snapshot_id}",
    response_model=ExecutionSnapshotMetadata,
)
def read_execution_snapshot_metadata(
    run_id: UUID, snapshot_id: UUID, user: CurrentUser, session: SessionDep
):
    run = owned_engine_run(session, run_id, user.id)
    publication, outcome = ExecutionSnapshotPublication, ExecutionSnapshotOutcome
    row = session.execute(
        select(publication, outcome)
        .select_from(publication)
        .outerjoin(
            outcome,
            and_(
                outcome.publication_id == publication.id,
                *(
                    getattr(outcome, k) == getattr(publication, k)
                    for k in ("owner_id", "run_id", "attempt_id", "generation")
                ),
            ),
        )
        .where(
            publication.id == snapshot_id,
            publication.owner_id == user.id,
            publication.run_id == run.id,
        )
    ).one_or_none()
    if row is None:
        raise DomainError("not_found", "Execution snapshot metadata not found", 404)
    return metadata_projection(run, *row)
