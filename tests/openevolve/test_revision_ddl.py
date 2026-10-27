"""Frozen SQL/driver checks; real PostgreSQL replay remains required."""

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

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
    calls = []

    class Connection:
        def exec_driver_sql(self, sql, parameters=None, *, execution_options=None):
            if sql == "SELECT current_schema()":
                return NS(scalar_one=lambda: "isolated_fixture")
            assert parameters is None
            assert execution_options == {"no_parameters": True}
            assert "@SCHEMA@" not in sql
            calls.append(sql)

    monkeypatch.setattr(module, "op", NS(get_bind=lambda: Connection()))
    getattr(module, direction)()
    assert len(calls) == 4
    assert "pg_try_advisory_xact_lock" in calls[0]
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in calls[1]
    assert 'SET LOCAL search_path = "isolated_fixture",pg_catalog' == calls[2]
    assert calls[3] == getattr(module, direction.upper() + "_SQL").replace(
        "@SCHEMA@", '"isolated_fixture"'
    )
