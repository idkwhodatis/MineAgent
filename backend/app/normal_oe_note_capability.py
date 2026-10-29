"""Explicit installation gate for typed normal OE note pins, not disclosure."""

from sqlalchemy import text

from backend.app.services import DomainError

REVISION = "032_normal_oe_note_pins"


def require_normal_oe_notes(session):
    """Reject unknown/multiple revisions before querying any new pin table."""
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() != [
        REVISION
    ]:
        raise DomainError(
            "normal_oe_notes_not_installed",
            "Typed normal execution notes require their explicit installed schema",
            409,
        )
