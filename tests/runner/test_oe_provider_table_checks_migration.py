"""Frozen additive provider CHECK repair and permanent realPG fixture wiring."""

import importlib.util
from pathlib import Path

import pytest

from backend.app import oe_provider_table_checks_sql as repair

ROOT = Path(__file__).parents[2]


def migration():
    spec = importlib.util.spec_from_file_location(
        "oe032c", ROOT / "backend/alembic/versions/032c_oe_provider_table_checks.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_frozen_provider_check_repair_has_exact_three_branches():
    value = migration()
    assert value.revision == "032c_oe_provider_table_checks"
    assert value.down_revision == "032b_oe_append_run_state"
    assert value.UPGRADE_SQL == repair.render_upgrade_sql()
    assert value.DOWNGRADE_SQL == repair.render_downgrade_sql()
    assert value.UPGRADE_SQL.count("ALTER TABLE") == 3
    assert "CREATE FUNCTION" not in value.UPGRADE_SQL
    assert "mineagent_oes_iteration_contract('context',context)" in value.UPGRADE_SQL
    assert (
        "CASE WHEN protocol='mineagent.oe-structured-provider-chat.v1'"
        in value.UPGRADE_SQL
    )
    assert "purpose='chat' AND ordinal=0" in value.UPGRADE_SQL
    assert "retained rows prevent downgrade" in value.DOWNGRADE_SQL
    assert value.DOWNGRADE_SQL.index("RAISE EXCEPTION") < value.DOWNGRADE_SQL.index(
        "ALTER TABLE"
    )


def test_provider_check_predecessor_tree_is_immutable(monkeypatch):
    repair.require_predecessors()
    monkeypatch.setattr(repair, "PREDECESSOR_TREE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="predecessors"):
        repair.render_upgrade_sql()
    with pytest.raises(ValueError, match="predecessors"):
        repair.render_downgrade_sql()


def test_provider_check_migration_keeps_maintenance_and_frozen_boundary():
    text = (
        ROOT / "backend/alembic/versions/032c_oe_provider_table_checks.py"
    ).read_text()
    assert "from backend" not in text
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in text
    assert "current_setting('transaction_isolation') <> 'read committed'" in text
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in text
    for table, _, _ in repair.TABLE_CHECKS:
        assert f'"{table}"' in text


def test_permanent_provider_check_matrix_is_not_skipped_or_simulated():
    path = ROOT / "tests/integration/oe_provider_table_checks_fixture.py"
    spec = importlib.util.spec_from_file_location("oe032c_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    sql = "\n".join(fixture.statements())
    assert "jsonb_populate_record" in sql and "EXCEPTION WHEN check_violation" in sql
    assert "Retained032c rows must prevent downgrade" in sql
    source = (
        (ROOT / "tests/integration/test_declared_upload_persistence.py")
        .read_text()
        .split("def test_oe_provider_row_checks_preserve_legacy_and_reject_drift", 1)[1]
    )
    assert "connection.exec_driver_sql" in source and "transaction.rollback()" in source
    assert "pytest.skip" not in source
