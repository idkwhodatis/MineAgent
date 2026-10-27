"""Exact-version pending peaks, never a detached quota grant."""

from sqlalchemy import select, text

from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.services import DomainError

SCHEMA = "mineagent.execution-snapshot-restore-closure.v1"


def _error(code="execution_snapshot_unverified"):
    return DomainError(code, "Snapshot capacity could not be verified", 409)


def snapshot_capability(session):
    from backend.app.schema_capabilities import capabilities_for

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise _error()
    return getattr(
        capabilities_for(revisions[0]), "execution_snapshot_schema", None
    ) in {SCHEMA, "mineagent.execution-snapshot-restore-closure.v2"}


def pending_snapshot_usage(session, owner_id):
    rows = session.scalars(
        select(ExecutionSnapshotPublication)
        .outerjoin(
            ExecutionSnapshotOutcome,
            ExecutionSnapshotOutcome.publication_id == ExecutionSnapshotPublication.id,
        )
        .where(ExecutionSnapshotOutcome.publication_id.is_(None))
        .limit(10001)
    ).all()
    if len(rows) > 10000 or any(
        type(row.peak_bytes) is not int
        or row.peak_bytes <= 0
        or type(row.peak_entries) is not int
        or not 1 <= row.peak_entries <= 256
        for row in rows
    ):
        raise _error("execution_snapshot_capacity")
    return (
        sum(row.peak_bytes for row in rows),
        sum(row.peak_bytes for row in rows if row.owner_id == owner_id),
        sum(row.peak_entries for row in rows),
    )


def pending_snapshot_usage_for_schema(session, owner_id):
    return (
        pending_snapshot_usage(session, owner_id)
        if snapshot_capability(session)
        else (0, 0, 0)
    )
