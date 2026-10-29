"""Owned already-released typed snapshot choices; no physical-read/launch claim."""

from sqlalchemy import String, and_, cast, literal, select

from backend.app.engine_result_release import release_bindings, source_key
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.models import DataUsageExposure
from backend.app.oe_structured_catalog_schemas import (
    StructuredSnapshotOption,
    StructuredSnapshotPage,
)
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest


def list_snapshots(session, owner_id, *, offset=0, limit=20):
    if (
        type(offset) is not int
        or type(limit) is not int
        or not 0 <= offset <= 10000
        or not 1 <= limit <= 50
    ):
        raise DomainError("invalid_page", "Bounded snapshot page required", 422)
    publication = ExecutionSnapshotPublication
    release_key = (
        literal("engine-result:execution_snapshot:")
        + cast(publication.id, String)
        + literal(":factor:v1")
    )
    rows = session.scalars(
        select(publication)
        .join(
            ExecutionSnapshotOutcome,
            and_(
                ExecutionSnapshotOutcome.owner_id == publication.owner_id,
                ExecutionSnapshotOutcome.publication_id == publication.id,
            ),
        )
        .join(
            DataUsageExposure,
            and_(
                DataUsageExposure.owner_id == publication.owner_id,
                DataUsageExposure.source_key == release_key,
            ),
        )
        .where(publication.owner_id == owner_id)
        .order_by(publication.created_at.desc(), publication.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    items = []
    for row in rows[:limit]:
        items.append(_option(session, owner_id, row))
    return StructuredSnapshotPage(
        items=tuple(items), offset=offset, limit=limit, has_more=len(rows) > limit
    )


def _option(session, owner_id, row):
    import json

    from backend.app.experiment_services import _identities
    from backend.app.factor_models import FactorVersion

    if row.owner_id != owner_id:
        raise DomainError("not_found", "Snapshot not found", 404)
    manifest = ExecutionSnapshotManifest.model_validate_json(
        json.dumps(row.manifest, allow_nan=False)
    )
    run, bindings = release_bindings(
        session, owner_id, row.run_id, row.id, "execution_snapshot", None
    )
    if len(bindings) != 1:
        raise DomainError("oe_snapshot_drift", "Snapshot release differs", 409)
    role, dataset, evidence = bindings[0]
    release = session.scalar(
        select(DataUsageExposure).filter_by(
            owner_id=owner_id,
            source_key=source_key("execution_snapshot", row.id, "factor"),
        )
    )
    version = session.get(FactorVersion, manifest.input.version.factor_version_id)
    expected_provenance = {
        **evidence,
        "role": "factor",
        "dataset_version_id": str(dataset.id),
        "dataset_content_sha256": dataset.content_sha256,
    }
    if (
        role != "factor"
        or release is None
        or version is None
        or version.owner_id != owner_id
        or digest(manifest) != row.manifest_sha256
        or release.dataset_version_id != dataset.id
        or (release.run_id, release.experiment_id, release.split_version_id)
        != (run.id, run.experiment_id, run.split_version_id)
        or (release.start_date, release.end_date)
        != (dataset.start_date, dataset.end_date)
        or release.purpose != "engine_execution_snapshot_release"
        or release.identities != _identities(session, dataset)
        or release.provenance != expected_provenance
    ):
        raise DomainError("oe_snapshot_drift", "Snapshot release differs", 409)
    return StructuredSnapshotOption(
        snapshot_id=row.id,
        factor_id=version.factor_id,
        factor_version_id=version.id,
        source_run_id=row.run_id,
        dataset_version_id=dataset.id,
        signal_window=manifest.input.window,
        snapshot_manifest_sha256=row.manifest_sha256,
        release_receipt_id=release.id,
    )
