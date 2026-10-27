"""Frozen SQL/driver checks; real PostgreSQL replay remains required."""

import ast
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, event

from backend.app.openevolve_revision_ddl import downgrade_sql, upgrade_sql

PATH = Path("backend/alembic/versions/024_openevolve_initial.py")


def revision():
    spec = importlib.util.spec_from_file_location("oe024_test_revision", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_revision_exactly_matches_reviewed_offline_assembly():
    module = revision()
    assert module.UPGRADE_SQL == upgrade_sql()
    assert module.DOWNGRADE_SQL == downgrade_sql()
    assert module.revision == "024_openevolve_initial"
    assert module.down_revision == "023_rd_submissions"
    assert len(module.TABLES) == 32
    assert module.UPGRADE_SQL.count("CREATE TABLE @SCHEMA@.") == 32
    for name in module.TABLES:
        assert f"EXISTS(SELECT 1 FROM @SCHEMA@.{name})" in module.DOWNGRADE_SQL
    assert "CASCADE" not in module.DOWNGRADE_SQL
    assert "WHERE engine='openevolve'" in module.DOWNGRADE_SQL
    assert "cost_microunits" in module.UPGRADE_SQL
    assert "offset_bytes%65536=0" in module.UPGRADE_SQL
    assert "offset_bytes%%65536=0" not in module.UPGRADE_SQL
    # A raw no-parameter migration must not retain a compiler's pyformat escape.
    # Audit all frozen migration literals; do not blanket-replace SQL percent
    # characters (modulo, ROWTYPE, LIKE and RAISE strings have distinct meanings).
    for path in PATH.parent.glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                assert "%%" not in node.value.value, (
                    path.name,
                    [getattr(t, "id", None) for t in node.targets],
                )


def test_every_new_shared_trigger_and_function_is_removed_on_empty_downgrade():
    module = revision()
    for trigger, table in re.findall(
        r"CREATE CONSTRAINT TRIGGER (\w+) .*? ON @SCHEMA@\.(\w+) .*?EXECUTE FUNCTION",
        module.UPGRADE_SQL,
    ):
        if table not in module.TABLES:
            assert (
                f"DROP TRIGGER {trigger} ON @SCHEMA@.{table};" in module.DOWNGRADE_SQL
            )
    names = re.findall(
        r"CREATE FUNCTION @SCHEMA@\.(mineagent_oe_\w+)\(", module.UPGRADE_SQL
    )
    assert len(names) == 18
    assert all(
        f"DROP FUNCTION @SCHEMA@.{name}(" in module.DOWNGRADE_SQL for name in names
    )


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_actual_migration_dispatch_never_parses_colons_or_percent_as_parameters(
    monkeypatch, direction
):
    module = revision()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            calls = []

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                calls.append(statement)
                return True  # Inspect actual DBAPI dispatch; no DDL runs on SQLite.

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                pytest.fail("Frozen024 DDL entered parameter interpolation")

            monkeypatch.setattr(module, "op", NS(get_bind=lambda: connection))
            monkeypatch.setattr(module, "_schema", lambda: '"isolated_fixture"')
            getattr(module, direction)()
            assert len(calls) == 4
            assert "pg_try_advisory_xact_lock" in calls[0]
            assert "ACCESS EXCLUSIVE MODE NOWAIT" in calls[1]
            assert 'SET LOCAL search_path = "isolated_fixture",pg_catalog' == calls[2]
            assert calls[3] == getattr(module, direction.upper() + "_SQL").replace(
                "@SCHEMA@", '"isolated_fixture"'
            )
            if direction == "upgrade":
                assert "offset_bytes%65536=0" in calls[3]
                assert "offset_bytes%%65536=0" not in calls[3]
    finally:
        engine.dispose()
