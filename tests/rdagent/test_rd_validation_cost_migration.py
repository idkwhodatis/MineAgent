"""Frozen append-only validation optimization and exact migration boundary."""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from backend.app import rdagent_validation_performance_sql as sql
from backend.app.core.db import REQUIRED_SCHEMA_REVISION
from script.generate_rd_validation_cost_migration import TABLES, document

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "backend/alembic/versions/032d_rd_validation_cost.py"


def migration():
    spec = importlib.util.spec_from_file_location("rd032d", PATH)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_frozen_migration_is_reproducible_and_exact_head():
    value = migration()
    assert PATH.read_text() == document()
    assert value.revision == REQUIRED_SCHEMA_REVISION == "032d_rd_validation_cost"
    assert value.down_revision == "032c_oe_provider_table_checks"
    assert value.UPGRADE_SQL == sql.render_upgrade_sql()
    assert value.DOWNGRADE_SQL == sql.render_downgrade_sql()
    assert value.UPGRADE_SQL.count("CREATE OR REPLACE FUNCTION") == 1
    assert value.DOWNGRADE_SQL == sql.canonical_original()
    assert "mineagent_rd_schema" not in value.UPGRADE_SQL + value.DOWNGRADE_SQL
    assert "DROP FUNCTION" not in value.DOWNGRADE_SQL
    assert "from backend" not in PATH.read_text()


def test_frozen_lock_inventory_covers_every_predecessor_application_table():
    expected = set()
    for path in (ROOT / "backend/alembic/versions").glob("*"):
        if path.suffix == ".sql":
            strings = [path.read_text()]
        elif path.suffix == ".py" and path.name < "032d":
            strings = [
                n.value
                for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
        else:
            continue
        for text in strings:
            expected.update(
                name
                for name in re.findall(
                    r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(?:@SCHEMA@\.)?([a-z][a-z0-9_]+)\s*\(",
                    text,
                    re.IGNORECASE,
                )
                if not name.startswith("procrastinate_")
            )
    assert set(TABLES) == expected
    assert len(TABLES) == len(set(TABLES))
    source = PATH.read_text()
    for table in TABLES:
        assert f'"{table}"' in source
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in source
    assert "current_setting('transaction_isolation') <> 'read committed'" in source
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in source


@pytest.mark.parametrize(
    "schema", [None, "", "PUBLIC", "x;DROP SCHEMA public", "x" * 64, True]
)
def test_schema_rejects_nonexact_identifiers(monkeypatch, schema):
    from types import SimpleNamespace

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
