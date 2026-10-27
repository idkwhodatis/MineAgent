"""Static exact020 migration composition; actual PostgreSQL runs separately."""

import ast
import json
import re
import sqlite3
from pathlib import Path

from backend.app.rdagent_controller_ddl import render_sql
from backend.app.rdagent_controller_revision import (
    _function,
    boundary_delta_sql,
    frozen_source_sql,
    profile_delta_sql,
)
from backend.app.schema_capabilities import capabilities_for
from packages.research_runtime.budget_contracts import BudgetVector


def literals():
    path = Path(__file__).parents[2] / "backend/alembic/versions/020_rd_controller.py"
    return {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.Assign)
    }


def test_static_explicit_composition():
    fixed = literals()
    assert fixed["revision"] == "020_rd_controller"
    assert fixed["down_revision"] == "019_native_hdf_storage"
    expected = f"{render_sql()}\n{profile_delta_sql()}\n{boundary_delta_sql()}".replace(
        '"public"', "@SCHEMA@"
    ).replace(
        "LANGUAGE plpgsql AS $$",
        "LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$",
    )
    assert fixed["UPGRADE_SQL"] == expected
    from backend.app.rdagent_provider_revision import function
    from backend.app.rdagent_receipt_schema_sql import receipt_schema_sql

    domain = receipt_schema_sql("controller")
    restored = domain.replace(
        "mineagent_rd_controller_schema", "mineagent_rd_schema"
    ).replace(" WHEN 'boolean' THEN NULL;\n", "")
    assert restored.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ) == function(frozen_source_sql(), "mineagent_rd_schema")
    assert "mineagent_rd_controller_schema(v,rule" in fixed["UPGRADE_SQL"]
    assert (
        "DROP FUNCTION @SCHEMA@.mineagent_rd_controller_schema(jsonb,jsonb,jsonb,integer)"
        in fixed["DOWNGRADE_SQL"]
    )
    assert (
        "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_schema("
        not in fixed["UPGRADE_SQL"]
    )
    for name in ("mineagent_rd_shared_consistent", "mineagent_rd_source_violation"):
        assert _function(frozen_source_sql(), name) in fixed["DOWNGRADE_SQL"]
    for name in ("UPGRADE_SQL", "DOWNGRADE_SQL"):
        amounts = re.findall(r"br.amounts<>'([^']+)'::jsonb", fixed[name])
        assert len(amounts) == 1
        assert json.loads(amounts[0]) == BudgetVector(mutation_slots=1).model_dump()
        for expression in (
            "CASE WHEN m.relation='continuation' THEN 'continuation' ELSE 'chat' END",
            "CASE WHEN t.ordinal=count_transports THEN 'completed' ELSE 'failed' END",
            "CASE WHEN idx=count_members-1 THEN 'stop' ELSE 'length' END",
        ):
            assert "(" + expression + ")" in fixed[name]
            assert "<>" + expression not in fixed[name]
    assert "mineagent.rdagent-orchestrator.v2" not in frozen_source_sql()
    assert "controller_child" not in frozen_source_sql()

    # Execute the exact boundary join against actual mapped column names. An
    # outcome belongs to a transport, NOT directly to a model operation.
    from backend.app.models import Base

    columns = {
        "factor_generation_model_operations": (
            "id",
            "operation_id",
            "call_id",
            "protocol",
            "request_ordinal",
        ),
        "research_model_operations": ("id", "state"),
        "research_model_transports": ("id", "operation_id", "state"),
        "research_model_outcomes": ("id", "transport_id", "outcome"),
    }
    assert "operation_id" not in Base.metadata.tables["research_model_outcomes"].c
    join = (
        fixed["UPGRADE_SQL"]
        .split("INTO count_members,members,outcomes ", 1)[1]
        .split(";", 1)[0]
    )
    query = "SELECT m.id,mo.id " + join.replace("c.id", ":call_id").replace(
        "c.kind", ":call_kind"
    )
    with sqlite3.connect(":memory:") as connection:
        for table, fields in columns.items():
            assert set(fields) <= set(Base.metadata.tables[table].c.keys())
            connection.execute(
                "CREATE TABLE "
                + table
                + " ("
                + ",".join(f + " TEXT" for f in fields)
                + ")"
            )
        connection.execute(
            "INSERT INTO factor_generation_model_operations VALUES ('member','op','call','mineagent.rdagent-chat.v1','0')"
        )
        connection.executemany(
            "INSERT INTO research_model_operations VALUES (?,?)",
            [("op", "completed"), ("other", "completed")],
        )
        connection.executemany(
            "INSERT INTO research_model_transports VALUES (?,?,?)",
            [
                ("failed", "op", "failed"),
                ("success", "op", "completed"),
                ("unrelated", "other", "completed"),
            ],
        )
        connection.executemany(
            "INSERT INTO research_model_outcomes VALUES (?,?,?)",
            [
                ("bad", "failed", "failed"),
                ("wanted", "success", "completed"),
                ("wrong", "unrelated", "completed"),
            ],
        )
        parameters = {"call_id": "call", "call_kind": "chat"}
        assert connection.execute(query, parameters).fetchall() == [
            ("member", "wanted")
        ]
        assert (
            connection.execute(
                query, {"call_id": "call", "call_kind": "embedding"}
            ).fetchall()
            == []
        )
        connection.execute(
            "UPDATE research_model_transports SET state='submitted' WHERE id='success'"
        )
        assert connection.execute(query, parameters).fetchall() == []


def test_versioned_capability_exact():
    before = capabilities_for("019_native_hdf_storage")
    after = capabilities_for("020_rd_controller")
    assert before.research_closure_schema.endswith(".v4")
    assert before.rd_source_closure_schema.endswith(".v1")
    assert after.research_closure_schema.endswith(".v5")
    assert after.rd_source_closure_schema.endswith(".v2")
    assert after.native_closure_schema == before.native_closure_schema


def test_no_historical_guard_replacement_or_unbounded_phase():
    sql = literals()["UPGRADE_SQL"]
    assert "DROP FUNCTION mineagent_neutral" not in sql
    assert "CREATE OR REPLACE FUNCTION mineagent_neutral" not in sql
    assert (
        "DROP TRIGGER rd_source_phase_closed ON factor_generation_outcomes" not in sql
    )
    assert (
        "DROP TRIGGER rd_source_phase_closed ON factor_evaluation_outcomes" not in sql
    )
    assert "controller_child" in sql and "uq_rd_boundary_execution_actor" in sql
    assert "conservative_unknown" in sql
    assert "DROP TABLE" not in literals()["DOWNGRADE_SQL"]


def test_both_directions_hold_maintenance_before_ddl(monkeypatch):
    import importlib.util
    from types import SimpleNamespace

    path = Path(__file__).parents[2] / "backend/alembic/versions/020_rd_controller.py"
    spec = importlib.util.spec_from_file_location("migration020", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from sqlalchemy import create_engine, event, text

    assert (
        text(module.UPGRADE_SQL)._bindparams and text(module.DOWNGRADE_SQL)._bindparams
    )
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            statements = []

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                statements.append(statement)
                return True  # Dispatch test only, not PostgreSQL DDL acceptance.

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                raise AssertionError("Literal020 DDL cannot be parameterized")

            class Bind:
                def exec_driver_sql(self, sql, **kwargs):
                    if sql == "SELECT current_schema()":
                        return SimpleNamespace(scalar_one=lambda: "fixture_schema")
                    assert kwargs == {"execution_options": {"no_parameters": True}}
                    return connection.exec_driver_sql(sql, **kwargs)

            monkeypatch.setattr(module.op, "get_bind", Bind)
            monkeypatch.setattr(module.op, "execute", statements.append)
            for direction in ("upgrade", "downgrade"):
                statements.clear()
                getattr(module, direction)()
                assert "pg_try_advisory_xact_lock" in statements[0]
                assert (
                    'SET LOCAL search_path = "fixture_schema",pg_catalog'
                    == statements[1]
                )
                assert "ACCESS EXCLUSIVE MODE NOWAIT" in statements[2]
                assert statements[-1] == getattr(
                    module, direction.upper() + "_SQL"
                ).replace("@SCHEMA@", '"fixture_schema"')
                if direction == "downgrade":
                    assert "Controller evidence prevents downgrade" in statements[3]
                    assert "DROP TRIGGER" in statements[4]
    finally:
        engine.dispose()
