"""Literal-only v4 revision preserves every frozen predecessor definition."""

import ast
from pathlib import Path

from backend.app.rdagent_controller_v4_revision import (
    SOURCES,
    frozen,
    render_downgrade_sql,
    render_upgrade_sql,
)
from backend.app.rdagent_provider_material_sql import render_upgrade_sql as material_sql

ROOT = Path(__file__).resolve().parents[2]


def literal(name):
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/027c_seeded_controller_v4.py").read_text()
    )
    return next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == name for t in n.targets)
    )


def test_v4_exact_literal_predecessor_and_restore():
    assert literal("revision") == "027c_seeded_controller_v4"
    assert literal("down_revision") == "027b_provider_material"
    assert literal("UPGRADE_SQL") == render_upgrade_sql()
    assert literal("DOWNGRADE_SQL") == render_downgrade_sql()
    assert frozen("mineagent_rd_provider_graph") == material_sql()
    assert all(frozen(name) in render_downgrade_sql() for name in SOURCES)


def test_seeded_only_and_unchanged_model_and_child_budgets():
    sql = render_upgrade_sql()
    assert "THEN 180000 WHEN" in sql and "THEN interval '180 seconds' WHEN" in sql
    assert "THEN 360000 WHEN" in sql
    assert "'seeded_controller_profile','mineagent.rdagent-orchestrator.v4'" in sql
    assert "Controller v4 requires exact provider seeded parent binding" in sql
    assert "PERFORM mineagent_rd_seed_check(sid)" in sql
    assert "br.amounts->>'calls'<>'0'" in sql
    assert "mineagent_rd_source_initial_step" not in sql
    assert "Retained controller v4 data forbids downgrade" in render_downgrade_sql()


def test_only_v4_graph_adds_parent_guard_query():
    before = frozen("mineagent_rd_provider_graph")
    after = render_upgrade_sql()
    start = after.index(
        "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_provider_graph("
    )
    graph = after[start : after.index("$$;", start) + 3]
    guard = "\n IF run.config->>'controller_profile'='mineagent.rdagent-orchestrator.v4' THEN\n  PERFORM mineagent_rd_v4_seed_binding(rid);\n END IF;"
    assert graph.count(guard) == 1
    restored = graph.replace(guard, "", 1).replace(
        "'mineagent.rdagent-orchestrator.v3','mineagent.rdagent-orchestrator.v4')",
        "'mineagent.rdagent-orchestrator.v3')",
    )
    assert restored == before
