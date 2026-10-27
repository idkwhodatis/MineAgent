"""Offline literal/composition checks. Genuine PostgreSQL acceptance is separate."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import execution_snapshot_sql as sql
from backend.app.normalizer_report_ddl import normalizer_report_sql
from backend.app.rdagent_execution_links_sql import (
    render_downgrade_sql,
    render_upgrade_sql,
)
from backend.app.schema_capabilities import capabilities_for

PATH = Path(__file__).parents[2] / "backend/alembic/versions/021_execution_snapshots.py"


def module():
    spec = importlib.util.spec_from_file_location("migration021", PATH)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_migration_is_exact_literal_composition_not_runtime_app_imports():
    value = module()
    assert value.revision == "021_execution_snapshots"
    assert value.down_revision == "020_rd_controller"
    assert value.NORMALIZER_RESULT_SQL == sql.NORMALIZER_SQL
    assert value.NORMALIZER_DOWNGRADE_SQL == sql.NORMALIZER_DOWNGRADE_SQL
    assert value.SNAPSHOT_SQL == sql.SNAPSHOT_SQL
    assert value.EVALUATION_SQL == sql.EVALUATION_SQL
    assert value.EVALUATION_CLAIM_SQL == sql.EVALUATION_CLAIM_SQL
    assert value.REPORT_INPUT_SQL == normalizer_report_sql()
    assert value.RD_UPGRADE_SQL == render_upgrade_sql()
    assert value.RD_DOWNGRADE_SQL == render_downgrade_sql()
    for node in ast.walk(ast.parse(PATH.read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not node.module.startswith(("backend.app", "packages"))


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_maintenance_and_guard_order_before_schema_mutation(monkeypatch, direction):
    value = module()
    statements = []
    monkeypatch.setattr(
        value.op,
        "get_bind",
        lambda: SimpleNamespace(
            exec_driver_sql=lambda _: SimpleNamespace(
                scalar_one=lambda: "fixture_schema"
            )
        ),
    )
    monkeypatch.setattr(value.op, "execute", statements.append)
    getattr(value, direction)()
    assert "READ COMMITTED" in statements[0]
    assert "pg_try_advisory_xact_lock" in statements[0]
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in statements[1]
    assert '"fixture_schema".research_objects' in statements[1]
    assert '"fixture_schema".factor_generation_observations' in statements[1]
    assert all("@SCHEMA@" not in item for item in statements)
    assert all("CASCADE" not in item for item in statements)
    if direction == "downgrade":
        guard = next(
            i
            for i, s in enumerate(statements)
            if "Report or unresolved normalizer intent blocks downgrade" in s
        )
        restore = next(
            i
            for i, s in enumerate(statements)
            if "CREATE OR REPLACE FUNCTION" in s
            and "mineagent_native_normalizer_consistent" in s
        )
        assert guard < restore
        assert any(
            "mineagent.numeric-process-extraction.v3" in s and "prevents downgrade" in s
            for s in statements
        )


def test_successor_capabilities_do_not_reinterpret_historical_versions():
    old = capabilities_for("020_rd_controller")
    new = capabilities_for("021_execution_snapshots")
    assert old.execution_snapshot_schema is None
    assert old.native_closure_schema.endswith(".v1")
    assert old.research_closure_schema.endswith(".v5")
    assert new.native_closure_schema.endswith(".v2")
    assert new.research_closure_schema.endswith(".v6")
    assert new.rd_source_closure_schema.endswith(".v3")
