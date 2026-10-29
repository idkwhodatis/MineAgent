"""New exact pair inheritance without reinterpreting any historical profile."""

# ruff: noqa: F811

import pytest
from test_rdagent_controller_backup import retained  # noqa: F401

from backend.app import rdagent_backup, research_backup
from backend.app.declared_upload_backup import closure_for
from backend.app.rdagent_profile_backup import require_config_revision
from backend.app.research_workflow_operations import enabled
from backend.app.schema_capabilities import capabilities_for


def test_030_capabilities_are_explicit_and_history_unchanged():
    new = capabilities_for("030_declared_upload_basket")
    old = capabilities_for("029_research_workflow")
    assert (new.research_closure_schema, new.rd_source_closure_schema) == (
        "mineagent.research-restore-closure.v15",
        "mineagent.rd-source-restore-closure.v10",
    )
    assert (old.research_closure_schema, old.rd_source_closure_schema) == (
        "mineagent.research-restore-closure.v14",
        "mineagent.rd-source-restore-closure.v9",
    )
    assert old.declared_upload_schema is None
    assert enabled(new) and enabled(old)
    assert research_backup.closure_fields(
        research_backup.SCHEMA_V15
    ) is research_backup.closure_fields(research_backup.SCHEMA_V14)
    assert rdagent_backup.closure_fields(
        rdagent_backup.SCHEMA_V10
    ) is rdagent_backup.closure_fields(rdagent_backup.SCHEMA_V9)


def test_new_pair_inherits_actual_controller_bytes_not_skipping_context(retained):
    retained["rd"]["schema_version"] = "mineagent.rd-source-restore-closure.v10"
    options = {"research_schema": research_backup.SCHEMA_V15, "seed_context": {}}
    with pytest.raises(ValueError, match="workflow context"):
        retained["validate"](**options)
    assert retained["validate"](**options, workflow_context={}) is True


@pytest.mark.parametrize("research,source", [(13, 9), (14, 8), (12, 9), (14, 7)])
def test_new_profile_rejects_crossed_historical_pairs(research, source):
    with pytest.raises(ValueError):
        require_config_revision(
            {"controller_profile": "mineagent.rdagent-orchestrator.v3"},
            research_schema=f"mineagent.research-restore-closure.v{research}",
            source_schema=f"mineagent.rd-source-restore-closure.v{source}",
        )


def test_new_lineage_cannot_be_omitted():
    with pytest.raises(ValueError):
        closure_for({}, capabilities_for("030_declared_upload_basket"))
