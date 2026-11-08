"""Exact PostgreSQL032e schema differentials, including Unicode and bad rules."""

import pytest
from sqlalchemy import text
from test_rdagent_validation_performance import (
    outcome,
    schema_pair,
)
from test_rdagent_validation_performance import (
    test_full_config_structurals_and_negative_mutations as full_config_corpus,
)
from test_rdagent_validation_performance import (
    test_schema_union_branch_recovery_and_malformed_rules as malformed_corpus,
)

from backend.app.rdagent_provider_revision import function, literal
from backend.app.rdagent_workflow_cost_sql import schema_original, schema_sql

pytestmark = pytest.mark.postgres


@pytest.fixture
def validation_db(postgres_engine):
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        sql = (
            function(
                literal("025_structured_factor_strategy.py"),
                "mineagent_structured_json_value",
            )
            + "\n"
            + schema_original().replace("mineagent_rd_schema", "old_rd_schema")
        )
        connection.exec_driver_sql(
            (sql + "\n" + schema_sql()).replace("@SCHEMA@", schema),
            execution_options={"no_parameters": True},
        )
    return postgres_engine


def test_all_control_characters_and_unicode(validation_db):
    rule = {"type": "string", "pattern": r"^[^\x00-\x1f\x7f]+$"}
    with validation_db.begin() as connection:
        for cp in range(1, 128):
            value = "中文😀" + chr(cp) + "é"
            assert schema_pair(connection, value, rule) == (
                "ok",
                cp >= 32 and cp != 127,
            )
        for value in ("", "é", "\u0085", "\u009f", "\u2028", "\u2029", "😀" * 8192):
            schema_pair(connection, value, rule)
        for value in ("x" * 8192, "中" * 8192, "😀" * 8192):
            assert schema_pair(connection, value, rule) == ("ok", True)
            assert schema_pair(connection, value + "\x01", rule) == ("ok", False)


def test_malformed_rules_and_nulls(validation_db):
    malformed_corpus(validation_db)
    # Every inline scalar path is checked nested in both container kinds; the
    # union alternatives must still recover from a rejected/malformed branch.
    corpus = [
        ("2024-02-30", {"type": "string", "format": "date"}),
        ("2024-01-01", {"type": "string", "format": "date"}),
        ("2024-01-01T00:00:00.000000Z", {"type": "string", "format": "date-time"}),
        ("x", {"type": "string", "pattern": "["}),
        ("x", {"type": "string", "minLength": "bad"}),
        ("x", {"type": "string", "enum": 1}),
        ("x", {"type": "string", "enum": ["x", "y"]}),
        ("x", {"type": "string", "const": "y"}),
        ("x", {"type": "string", "title": "CalculationUniverse"}),
        ("x", {"type": "string", "$ref": "#/$defs/missing"}),
        (1, {"type": "integer", "maximum": "bad"}),
        (-1, {"type": "integer"}),
        (True, {"type": "integer"}),
        (True, {"type": "boolean"}),
        (None, {"type": "null"}),
        (1.5, {"type": "number", "exclusiveMinimum": 1.5}),
        (1.5, {"type": "number", "minimum": "bad"}),
        (1.5, {"type": "number"}),
    ]
    with validation_db.begin() as connection:
        for value, rule in corpus:
            for kind in ("array", "object"):

                def wrap(child, kind=kind):
                    return {
                        "type": kind,
                        **(
                            {"items": child}
                            if kind == "array"
                            else {"properties": {"x": child}}
                        ),
                    }

                nested = [value] if kind == "array" else {"x": value}
                for depth in (-1, 0, 23, 24, 25, None):
                    schema_pair(connection, nested, wrap(rule), depth=depth)
                for union in ("anyOf", "oneOf"):
                    # A type-only scalar branch provides a valid alternative.
                    good_type = (
                        "null"
                        if value is None
                        else "boolean"
                        if type(value) is bool
                        else "string"
                        if isinstance(value, str)
                        else "number"
                    )
                    good = wrap({"type": good_type})
                    for branches in ([wrap(rule), good], [good, wrap(rule)]):
                        schema_pair(connection, nested, {union: branches})
        for token in ("1e-400", "1e400"):
            for raw, rule in (
                (
                    '{"x":' + token + "}",
                    '{"type":"object","properties":{"x":{"type":"number"}}}',
                ),
                ("[" + token + "]", '{"type":"array","items":{"type":"number"}}'),
            ):
                params = {"value": raw, "rule": rule}
                query = "SELECT {}(CAST(:value AS jsonb), CAST(:rule AS jsonb), '{}'::jsonb)"
                # Literal braces belong to SQL, not Python format placeholders.
                for name in ("old_rd_schema", "mineagent_rd_schema"):
                    assert outcome(
                        connection, query.replace("{}(", name + "(", 1), params
                    ) == ("ok", False)


def test_real_config_structures_and_negative_mutations(validation_db):
    full_config_corpus(validation_db)


def test_schema_text_loop_probe(validation_db, capsys):
    """Diagnostic varying long-text workload, never a timing acceptance gate."""
    import json

    rule = json.dumps({"type": "string", "pattern": r"^[^\x00-\x1f\x7f]+$"})
    with validation_db.begin() as connection:
        for name in ("old_rd_schema", "mineagent_rd_schema"):
            result = connection.execute(
                text(
                    f"EXPLAIN (ANALYZE, FORMAT JSON) SELECT count(*) FILTER (WHERE {name}("
                    "to_jsonb(repeat('中文', 2000) || i::text), CAST(:rule AS jsonb), '{}'::jsonb)) "
                    "FROM generate_series(1, 40) i"
                ),
                {"rule": rule},
            ).scalar_one()
            print(name, result)


def test_frozen_workflow_migration_roundtrip_preserves_other_contracts(
    migrated_engine,
):
    """Exercise actual Alembic downgrade/upgrade, then roll back the whole probe."""
    import re
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from backend.app import rdagent_workflow_cost_sql as module

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/alembic"))
    optimized = {
        "mineagent_rd_schema",
        "mineagent_rd_workflow_submission_check",
        "mineagent_oes_provider_check",
        "mineagent_oes_controller_check",
    }

    def state(connection):
        functions = dict(
            connection.exec_driver_sql("""
            SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
                   pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=current_schema() AND p.prokind='f'
        """).all()
        )
        checks = dict(
            connection.exec_driver_sql("""
            SELECT c.relname || ':' || k.conname, pg_get_constraintdef(k.oid)
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND k.contype='c'
        """).all()
        )
        return functions, checks

    def assert_bodies(connection, rendered, schema):
        bodies = dict(
            connection.exec_driver_sql("""
            SELECT p.proname,p.prosrc FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=current_schema() AND p.proname IN
              ('mineagent_rd_schema','mineagent_rd_workflow_submission_check',
               'mineagent_oes_provider_check','mineagent_oes_controller_check')
        """).all()
        )
        expected = dict(
            re.findall(
                r"CREATE OR REPLACE FUNCTION @SCHEMA@\.([a-z_]+)\(.*?AS \$\$(.*?)\$\$;",
                rendered,
                re.DOTALL,
            )
        )
        assert expected
        assert bodies == {
            name: body.replace("@SCHEMA@", schema) for name, body in expected.items()
        }

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = (
                '"'
                + connection.exec_driver_sql("SELECT current_schema()").scalar_one()
                + '"'
            )
            config.attributes["connection"] = connection
            command.downgrade(config, "032e_rd_workflow_cost")
            before = state(connection)
            assert_bodies(connection, module.render_upgrade_sql(), schema)
            command.downgrade(config, "032d_rd_validation_cost")
            assert_bodies(connection, module.render_downgrade_sql(), schema)
            predecessor = state(connection)
            assert predecessor[1] == before[1]
            assert {
                k: v
                for k, v in predecessor[0].items()
                if k.split("(", 1)[0] not in optimized
            } == {
                k: v
                for k, v in before[0].items()
                if k.split("(", 1)[0] not in optimized
            }
            command.upgrade(config, "032e_rd_workflow_cost")
            assert_bodies(connection, module.render_upgrade_sql(), schema)
            assert state(connection) == before
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).all() == [("032e_rd_workflow_cost",)]
        finally:
            transaction.rollback()
