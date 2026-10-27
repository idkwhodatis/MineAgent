"""Frozen SQL assembly checks only; actual PostgreSQL execution is separate."""

import ast
from pathlib import Path

from backend.app.openevolve_revision_ddl import _check, historical
from backend.app.structured_revision_ddl import (
    CHECKS,
    SHARED,
    TABLES,
    downgrade_sql,
    upgrade_sql,
)


def literal(name):
    module = ast.parse(
        Path("backend/alembic/versions/025_structured_factor_strategy.py").read_text()
    )
    return next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    )


def test_frozen025_is_exact_assembler_and_predecessor():
    assert literal("revision") == "025_structured_factor_strategy"
    assert literal("down_revision") == "024_openevolve_initial"
    assert literal("UPGRADE_SQL") == upgrade_sql()
    assert literal("DOWNGRADE_SQL") == downgrade_sql()
    assert literal("TABLES") == TABLES
    assert literal("SHARED") == SHARED


def test_all024_check_branches_are_preserved_verbatim():
    old = historical("024", "UPGRADE_SQL")
    for _, name, _ in CHECKS:
        assert _check(old, name) in upgrade_sql()
        assert _check(old, name) in downgrade_sql()
    assert "actual_engine IN ('rdagent','openevolve','structured')" in upgrade_sql()
    assert "actual_engine IN ('rdagent','openevolve')" in downgrade_sql()


def test_no_broker_authority_or_unbound_structured_process():
    sql = upgrade_sql()
    assert "Closed structured engine has no model or policy IPC" in sql
    assert "Structured process requires actual structured run" in sql
    assert (
        "role='native_evaluator' AND profile='mineagent.factor-strategy-engine.v1'"
        in sql
    )
    assert "ipc_policy='none' AND ticket_sha256 IS NULL" in sql
    for name in TABLES:
        assert f"BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{name}" in sql
    for name in SHARED:
        assert f"AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{name}" in sql


def test_retained_structured_evidence_blocks_lossy_downgrade():
    sql = downgrade_sql()
    for name in TABLES:
        assert f"EXISTS(SELECT 1 FROM @SCHEMA@.{name})" in sql
    assert "structured_factor_execution_source" in sql
    assert "mineagent.factor-strategy-engine.v1" in sql
    assert " CASCADE" not in sql
