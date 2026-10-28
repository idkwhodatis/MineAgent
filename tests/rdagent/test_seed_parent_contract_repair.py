"""Additive seed-parent repair contracts; actual PG tests live in integration."""

import ast
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from backend.app.rdagent_models import FactorGenerationStart
from backend.app.seed_parent_contract_repair import DOWNGRADE_SQL, UPGRADE_SQL

ROOT = Path(__file__).resolve().parents[2]


def test_orm_only_removes_obsolete_null_parent_requirement():
    checks = {
        c.name: str(c.sqltext)
        for c in FactorGenerationStart.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert checks["ck_rd_source_initial_step"] == "logical_step=0"
    assert any("generation > 0" in value for value in checks.values())
    assert any(
        isinstance(c, ForeignKeyConstraint)
        and tuple(c.column_keys) == ("owner_id", "parent_factor_version_id")
        for c in FactorGenerationStart.__table__.constraints
    )


def test_repair_does_not_remove_receipt_guards_or_foreign_keys():
    assert UPGRADE_SQL.count("DROP CONSTRAINT") == 1
    assert "DROP TRIGGER" not in UPGRADE_SQL
    assert "CASCADE" not in UPGRADE_SQL
    assert "CHECK (logical_step=0)" in UPGRADE_SQL
    assert DOWNGRADE_SQL.index("downgrade refused") < DOWNGRADE_SQL.index("ALTER TABLE")
    assert "WHERE parent_factor_version_id IS NOT NULL" in DOWNGRADE_SQL
    assert (
        "CHECK (logical_step=0 AND parent_factor_version_id IS NULL)" in DOWNGRADE_SQL
    )


def test_literal_successor_preserves_historical_seed_checks():
    from script.generate_contract_repairs_migration import document

    target = ROOT / "backend/alembic/versions/027a_contract_repairs.py"
    assert document() == target.read_text()
    parsed = ast.parse(target.read_text())
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in parsed.body
        if isinstance(node, ast.Assign)
    }
    assert assignments["revision"] == "027a_contract_repairs"
    assert assignments["down_revision"] == "027_rd_controller_v3"
    assert UPGRADE_SQL in assignments["UPGRADE_SQL"]
    assert DOWNGRADE_SQL in assignments["DOWNGRADE_SQL"]
    old = ast.parse(
        (ROOT / "backend/alembic/versions/026_rd_seeded_submissions.py").read_text()
    )
    sql = next(
        ast.literal_eval(n.value)
        for n in old.body
        if isinstance(n, ast.Assign) and n.targets[0].id == "UPGRADE_SQL"
    )
    assert (
        "A first-generation parent requires its prior immutable seeded receipt" in sql
    )
    assert "Seeded initial source must retain declared parent and factor family" in sql
