"""Exact installed typed research-input inventory capability."""


def input_inventory_enabled(session):
    """Exact installed capability, shared by every factor-pool publisher."""
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for
    from backend.app.services import DomainError

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    try:
        if len(revisions) != 1:
            raise ValueError("One installed revision required")
        capability = capabilities_for(revisions[0])
    except ValueError:
        raise DomainError(
            "schema_version",
            "Research input inventory requires a supported schema",
            409,
        ) from None
    enabled = revisions[0] in {
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
        "032b_oe_append_run_state",
    }
    expected = "mineagent.research-inputs.v1" if enabled else None
    if capability.research_input_schema != expected:
        raise DomainError(
            "schema_version", "Research input inventory capability differs", 409
        )
    return enabled
