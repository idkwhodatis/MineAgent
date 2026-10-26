"""Bounded pending source peak accounting; no quota grant or settlement."""

from sqlalchemy import select

from backend.app.rdagent_models import (
    FactorSourcePublication,
    FactorSourcePublicationOutcome,
)
from backend.app.services import DomainError

MAX_PENDING_SOURCES = 10000
SOURCE_PEAK_ENTRIES = 5


def pending_source_usage(session, owner_id):
    """Caller already owns the shared factor quota lock/physical admission.

    Count unfinished publications across all owners/attempts, including lost
    fences. Physical final bytes are counted independently and never credited
    here. Returns installation bytes, requested owner bytes, installation entries.
    Revision018 schema is required; missing tables must not mean zero usage.
    """
    rows = session.scalars(
        select(FactorSourcePublication)
        .outerjoin(
            FactorSourcePublicationOutcome,
            FactorSourcePublicationOutcome.publication_id == FactorSourcePublication.id,
        )
        .where(FactorSourcePublicationOutcome.publication_id.is_(None))
        .limit(MAX_PENDING_SOURCES + 1)
    ).all()
    if len(rows) > MAX_PENDING_SOURCES or any(
        type(row.peak_bytes) is not int or row.peak_bytes <= 0 for row in rows
    ):
        raise DomainError(
            "factor_capacity", "Pending source inventory exceeds bound", 409
        )
    return (
        sum(row.peak_bytes for row in rows),
        sum(row.peak_bytes for row in rows if row.owner_id == owner_id),
        SOURCE_PEAK_ENTRIES * len(rows),
    )


def pending_source_usage_for_schema(session, owner_id):
    """Historical exact revisions have no source table; unknown revisions fail."""
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise DomainError("factor_capacity", "Exact schema revision required", 409)
    capability = capabilities_for(revisions[0])
    if capability.rd_source_closure_schema is None:
        return 0, 0, 0
    return pending_source_usage(session, owner_id)
