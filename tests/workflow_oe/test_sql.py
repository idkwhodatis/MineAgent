"""Frozen predecessor safety and explicit031 SQL assembly invariants."""

from pathlib import Path

import pytest

from backend.app import oe_workflow_dispatch_sql as dispatch
from backend.app import oe_workflow_predecessors as predecessors
from backend.app.oe_workflow_revision import (
    RESTORE,
    render_downgrade_sql,
    render_upgrade_sql,
)
from script.generate_workflow_structured_stage_migration import document

ROOT = Path(__file__).resolve().parents[2]


def test_generated_literal_exact():
    assert (
        document()
        == (
            ROOT / "backend/alembic/versions/031_workflow_structured_stage.py"
        ).read_text()
    )


@pytest.mark.parametrize("revision", tuple(predecessors.PINS))
def test_each_predecessor_pin_fails_closed(monkeypatch, revision):
    monkeypatch.setitem(predecessors.PINS, revision, "0" * 64)
    # Select a function retained only in028, visiting all three predecessor pins.
    with pytest.raises(ValueError):
        predecessors.predecessor("mineagent_oes_candidate_check")


def test_dispatch_changes_only_exact_version_predicates():
    new = dispatch.render_upgrade_sql()
    for name in dispatch.COUNTS:
        assert predecessors.predecessor(name) in dispatch.render_downgrade_sql()
    assert (
        "IN ('mineagent.oe-structured-provider-run.v1','mineagent.oe-structured-provider-run.v2')"
        in new
    )
    assert "IS NOT TRUE" in new


def test_all_replaced_predicates_restored_and_new_history_blocks_down():
    up, down = render_upgrade_sql(), render_downgrade_sql()
    for name in RESTORE:
        assert "FUNCTION @SCHEMA@." + name + "(" in up
        assert "FUNCTION @SCHEMA@." + name + "(" in down
    assert down.index("Workflow OE history prevents downgrade") < down.index(
        "DROP TRIGGER"
    )
    assert "workflow_stage" in down and "workflow_seed" in down


def test_physical_budgets_and_scope_survive_explicit_new_bootstrap():
    sql = render_upgrade_sql() + predecessors.predecessor("mineagent_oes_native_check")
    for text in (
        "interval '600 seconds'",
        "'wall_milliseconds',600000",
        "'storage_bytes',85590016",
        "'evaluator_executions',1,'wall_milliseconds',90000",
        "'conservative_unknown'",
        "'launch_identity'",
        "'controller_completion'",
    ):
        assert text in sql
    assert "mineagent_oe_workflow_consumption" in sql
    assert "FOREACH v IN ARRAY versions" in sql


def test_original_028_table_renderer_remains_frozen():
    from backend.app.oe_structured_persistence_ddl import tables

    # Metadata discovery must retain the same original table inventory. The
    # original full migration generator equality is tested by its existing suite.
    assert "oe_workflow_submissions" not in tables()
