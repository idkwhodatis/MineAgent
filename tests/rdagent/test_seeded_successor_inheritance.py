"""Unpublished successors preserve the installed seeded-only profile authority."""

from backend.app import (
    oe_structured_process_extension_sql,
    oe_structured_shared_process_sql,
    oe_structured_shared_sql,
    rdagent_workflow_provider_revision,
    rdagent_workflow_revision,
    rdagent_workflow_sql,
)
from backend.app.openevolve_revision_ddl import historical
from backend.app.rdagent_provider_revision import function


def test_028_shared_functions_restore_exact_seeded_predecessor():
    previous = historical("027c", "UPGRADE_SQL")
    assert (
        function(previous, "mineagent_rd_shared_consistent")
        in oe_structured_shared_sql.render_downgrade_sql()
    )
    for name in (
        "mineagent_rd_controller_receipt_order",
        "mineagent_rd_controller_consistent",
    ):
        assert function(
            previous, name
        ) == oe_structured_shared_process_sql.frozen_function(name)
        assert (
            function(previous, name)
            in oe_structured_shared_process_sql.render_downgrade_sql()
        )
    assert (
        "mineagent.rdagent-orchestrator.v4"
        in oe_structured_process_extension_sql.render_upgrade_sql()
    )
    assert (
        "mineagent.rdagent-orchestrator.v4"
        in oe_structured_process_extension_sql.render_downgrade_sql()
    )


def test_029_preserves_v4_guard_and_restores_exact_028_predecessors():
    previous = historical("027c", "UPGRADE_SQL")
    module = rdagent_workflow_provider_revision
    for name in (
        "mineagent_rd_source_config",
        "mineagent_rd_provider_graph",
        "mineagent_rd_operational_boundary_consistent",
        "mineagent_rd_provider_queued_config",
    ):
        assert module.original(name) == function(previous, name)
        assert module.original(name) in module.render_downgrade_sql()
    assert "PERFORM mineagent_rd_v4_seed_binding(rid)" in module.render_upgrade_sql()
    assert "THEN 180000 WHEN" in rdagent_workflow_revision.controller_sql()
    assert (
        rdagent_workflow_revision.predecessor("mineagent_rd_controller_consistent")
        in rdagent_workflow_revision.render_downgrade_sql()
    )


def test_workflow_config_cannot_inherit_seeded_manual_profile():
    sql = rdagent_workflow_sql.render_upgrade_sql()
    guard = "IF v ? 'controller_profile' AND v->'controller_profile' IS DISTINCT FROM '\"mineagent.rdagent-orchestrator.v3\"'::jsonb THEN RETURN false; END IF;"
    assert guard in sql
    assert sql.index(guard) < sql.index("IF mineagent_rd_provider_config(base)")


def test_031_literal_restores_all_current_030_predecessors():
    from backend.app.oe_workflow_predecessors import predecessor
    from backend.app.oe_workflow_revision import RESTORE, replaceable

    down = historical("031", "DOWNGRADE_SQL")
    for name in RESTORE:
        assert replaceable(predecessor(name)) in down
    assert "mineagent.rdagent-orchestrator.v4" in down
    assert "THEN 180000 WHEN" in down
