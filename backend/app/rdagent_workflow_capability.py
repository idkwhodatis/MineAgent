"""Exact028 workflow RD capability; no future-schema wildcard."""

from sqlalchemy import text

from backend.app.services import DomainError

REVISION = "029_research_workflow"


def require_workflow_revision(session):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
    ):
        raise DomainError(
            "workflow_child_unavailable", "Workflow child schema unavailable", 409
        )
