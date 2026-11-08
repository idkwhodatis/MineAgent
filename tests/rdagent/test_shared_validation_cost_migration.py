"""Frozen032f reconstruction, complete maintenance locks, and exact rollback."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import oe_outbox_guard_sql as guard_sql
from backend.app import schema_reference_cost_sql as schema_sql
from backend.app import structured_raw_cost_sql as raw_sql
from backend.app.core.db import REQUIRED_SCHEMA_REVISION
from script.generate_rd_validation_cost_migration import TABLES
from script.generate_shared_validation_cost_migration import document

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "backend/alembic/versions/032f_shared_validation_cost.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared032f", PATH)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_frozen_shared_migration_reconstructs_only_reviewed_functions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    value = migration()
    assert PATH.read_text() == document()
    assert value.revision == REQUIRED_SCHEMA_REVISION == "032f_shared_validation_cost"
    assert value.down_revision == "032e_rd_workflow_cost"
    assert ScriptDirectory.from_config(Config("backend/alembic.ini")).get_heads() == [
        value.revision
    ]
    renderers = (raw_sql, schema_sql, guard_sql)
    assert value.UPGRADE_SQL == "\n".join(
        module.render_upgrade_sql() for module in renderers
    )
    assert value.DOWNGRADE_SQL == "\n".join(
        module.render_downgrade_sql() for module in reversed(renderers)
    )
    assert value.UPGRADE_SQL.count("CREATE OR REPLACE FUNCTION") == 3
    assert value.DOWNGRADE_SQL.count("CREATE OR REPLACE FUNCTION") == 2
    assert value.DOWNGRADE_SQL.count("DROP FUNCTION") == 1
    assert value.UPGRADE_SQL.count("CREATE CONSTRAINT TRIGGER") == 2
    assert value.DOWNGRADE_SQL.count("DROP TRIGGER") == 2
    assert "DROP FUNCTION @SCHEMA@." + schema_sql.HELPER in value.DOWNGRADE_SQL
    assert "from backend" not in PATH.read_text()


def test_shared_maintenance_exactly_preserves_predecessor_implementation():
    predecessor = ROOT / "backend/alembic/versions/032e_rd_workflow_cost.py"
    assert PATH.read_text().split("# fmt: on", 1)[1] == predecessor.read_text().split(
        "# fmt: on", 1
    )[1].replace("RD workflow cost migration", "Shared validation cost migration")
    tree = ast.parse(PATH.read_text())
    maintenance = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_maintenance"
    )
    tables = next(
        ast.literal_eval(node.value)
        for node in maintenance.body
        if isinstance(node, ast.Assign)
    )
    assert tables == TABLES
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in PATH.read_text()
    assert (
        "current_setting('transaction_isolation') <> 'read committed'"
        in PATH.read_text()
    )
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in PATH.read_text()


@pytest.mark.parametrize(
    "schema", [None, "", "PUBLIC", "x;DROP SCHEMA public", "x" * 64, True]
)
def test_shared_schema_rejects_nonexact_identifiers(monkeypatch, schema):
    value = migration()
    monkeypatch.setattr(
        value.op,
        "get_bind",
        lambda: SimpleNamespace(
            exec_driver_sql=lambda _: SimpleNamespace(scalar_one=lambda: schema)
        ),
    )
    with pytest.raises(RuntimeError, match="Exact installation schema required"):
        value._schema()


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_shared_migration_requires_maintenance_before_executing_sql(
    monkeypatch, direction
):
    value = migration()
    calls = []
    monkeypatch.setattr(value, "_schema", lambda: '"test_schema"')
    monkeypatch.setattr(value.op, "execute", lambda sql: calls.append(sql))
    monkeypatch.setattr(
        value.op,
        "get_bind",
        lambda: SimpleNamespace(
            exec_driver_sql=lambda sql, **kwargs: calls.append((sql, kwargs))
        ),
    )
    getattr(value, direction)()
    assert len(calls) == 4
    assert "pg_try_advisory_xact_lock" in calls[0]
    assert (
        calls[1]
        == "LOCK TABLE "
        + ",".join('"test_schema".' + name for name in TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert calls[2] == 'SET LOCAL search_path = "test_schema",pg_catalog'
    assert calls[3] == (
        getattr(value, direction.upper() + "_SQL").replace("@SCHEMA@", '"test_schema"'),
        {"execution_options": {"no_parameters": True}},
    )


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_failed_maintenance_never_replaces_functions(monkeypatch, direction):
    value = migration()
    monkeypatch.setattr(value, "_schema", lambda: '"test_schema"')

    def refused(_):
        raise RuntimeError("maintenance refused")

    monkeypatch.setattr(value, "_maintenance", refused)
    monkeypatch.setattr(
        value.op, "get_bind", lambda: pytest.fail("SQL executed after refusal")
    )
    with pytest.raises(RuntimeError, match="maintenance refused"):
        getattr(value, direction)()
