"""Exact additive032b recipe; PostgreSQL acceptance is separately installed."""

import ast
import importlib.util
from pathlib import Path

import pytest

from backend.app import oe_append_fence_repair_sql as repair

ROOT = Path(__file__).parents[2]


def migration():
    path = ROOT / "backend/alembic/versions/032b_oe_append_run_state.py"
    spec = importlib.util.spec_from_file_location("oe032b_test", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_oe_append_repair_changes_only_nonexistent_run_field_guard():
    value = migration()
    old, new = repair.previous_function(), repair.render_upgrade_sql()
    assert value.revision == "032b_oe_append_run_state"
    assert value.down_revision == "032a_rd_terminal_results"
    assert value.UPGRADE_SQL == new
    assert value.DOWNGRADE_SQL == old == repair.render_downgrade_sql()
    assert new == old.replace(" OR r.finished_at IS NOT NULL", "")
    assert "r.finished_at" not in new
    assert "a.finished_at IS NOT NULL" in new
    assert "a.lease_expires_at<=clock_timestamp()" in new
    assert "a.generation IS DISTINCT FROM r.generation" in new
    assert "IF r.status IS DISTINCT FROM 'running'" in new
    assert "ALTER TABLE" not in new


def test_oe_append_repair_pins_every_intervening_predecessor(monkeypatch):
    original = repair.historical
    for changed in ("031", "032", "032a"):

        def altered(revision, name, *, changed=changed):
            return original(revision, name) + (" " if revision == changed else "")

        monkeypatch.setattr(repair, "historical", altered)
        with pytest.raises(ValueError, match="predecessor"):
            repair.render_upgrade_sql()
    monkeypatch.setattr(repair, "historical", original)


def test_oe_append_migration_quiescence_and_frozen_import_boundary():
    path = ROOT / "backend/alembic/versions/032b_oe_append_run_state.py"
    tree = ast.parse(path.read_text())
    assert [
        (n.module, [a.name for a in n.names])
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    ] == [("alembic", ["op"])]
    text = path.read_text()
    assert "current_setting('transaction_isolation') <> 'read committed'" in text
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in text
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in text
    maintenance = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_maintenance"
    )
    tables = next(
        ast.literal_eval(n.value)
        for n in maintenance.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "tables"
    )
    from backend.app.oe_structured_trigger_sql import TABLES

    assert set(tables) == {"research_runs", "execution_attempts", *TABLES}
    assert len(tables) == len(set(tables))


def test_oe_append_permanent_real_pg_fixture_is_wired_without_skips():
    path = ROOT / "tests/integration/oe_append_fence_fixture.py"
    spec = importlib.util.spec_from_file_location("oe032b_fixture_test", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    sql = "\n".join(fixture.statements())
    assert (
        "CREATE TABLE oe032b_append_probe.research_runs AS SELECT * FROM research_runs WITH NO DATA"
        in sql
    )
    assert "42703" in sql and "OE032B_APPEND_MATRIX_COMPLETE" in sql
    assert "lease_expires_at=NULL" in sql and "finished_at=clock_timestamp()" in sql
    assert "OE032B_ISOLATION_REJECTED" in "\n".join(fixture.isolation_statements())
    source = (
        ROOT / "tests/integration/test_declared_upload_persistence.py"
    ).read_text()
    body = source.split(
        "def test_oe_append_fence_repair_preserves_current_attempt_guards", 1
    )[1]
    assert "REPEATABLE READ" in body and "transaction.rollback()" in body
    assert "pytest.skip" not in body
