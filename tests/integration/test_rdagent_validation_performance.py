"""Real PostgreSQL predecessor differential and frozen migration roundtrip.

Unset TEST_DATABASE_URL explicitly skips execution. These tests compare the
schema interpreter, not the complete relational admission/hash predicates.
"""

import copy
import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import rdagent_validation_performance_sql as module
from backend.app.rdagent_provider_revision import function, literal

pytestmark = pytest.mark.postgres
LIMIT = 1048576
_DEFAULT_DEFS = object()


@pytest.fixture
def validation_db(postgres_engine):
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        sql = "\n".join(
            (
                function(
                    literal("025_structured_factor_strategy.py"),
                    "mineagent_structured_json_value",
                ),
                module.canonical_original().replace(
                    "mineagent_rd_canonical_json", "old_rd_canonical_json"
                ),
                module.schema_original().replace(
                    "mineagent_rd_schema", "old_rd_schema"
                ),
                module.schema_original(),
                module.render_upgrade_sql(),
            )
        )
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", schema), execution_options={"no_parameters": True}
        )
    return postgres_engine


def outcome(connection, sql, params):
    """A rejected input must not poison the transaction or the next case."""
    try:
        with connection.begin_nested():
            return ("ok", connection.scalar(text(sql), params))
    except DBAPIError as error:
        return ("error", error.orig.sqlstate)


def canonical_pair(connection, raw, ascii_only=True, depth=0):
    params = {"v": raw, "ascii": ascii_only, "depth": depth}
    query = "SELECT {}(CAST(:v AS jsonb), :ascii, :depth)"
    old = outcome(connection, query.format("old_rd_canonical_json"), params)
    new = outcome(connection, query.format("mineagent_rd_canonical_json"), params)
    assert new == old
    return new


def schema_pair(
    connection,
    value,
    rule,
    defs=_DEFAULT_DEFS,
    depth=0,
    sql_null=False,
    sql_null_rule=False,
    sql_null_defs=False,
):
    params = {
        "v": None if sql_null else json.dumps(value),
        "r": None if sql_null_rule else json.dumps(rule),
        "defs": None
        if sql_null_defs
        else json.dumps({} if defs is _DEFAULT_DEFS else defs),
        "depth": depth,
    }
    query = (
        "SELECT {}(CAST(:v AS jsonb), CAST(:r AS jsonb), CAST(:defs AS jsonb), :depth)"
    )
    old = outcome(connection, query.format("old_rd_schema"), params)
    new = outcome(connection, query.format("mineagent_rd_schema"), params)
    assert new == old
    return new


def test_canonical_batched_scalar_unicode_and_depth_differential(
    validation_db, tmp_path
):
    from tests.rdagent.test_rdagent_canonical_sql import escaped_canonical_cases

    escaped = escaped_canonical_cases(tmp_path)
    assert len(escaped) == 1639
    strings = [
        "",
        "plain",
        'quote" slash/ backslash\\',
        "".join(chr(i) for i in range(1, 32)),
        "\x7f",
        "é中",
        "😀",
        "e\u0301",
        "\u2028\u2029",
    ]
    values = [None, True, False, 0, -1, 10**80, [], {}, *strings]
    values += [[s, 1, False, None] for s in strings]
    values += [{s: s, "z": [1, True, None]} for s in strings]
    values += escaped
    values += [
        {"nested": [escaped[17], {'controls\n\\"': escaped[28]}], "😀": escaped[-1]},
        [escaped[-1], {"é": [escaped[30], None, False]}, escaped[16]],
    ]
    with validation_db.begin() as connection:
        for ascii_only in (True, False):
            for value in values:
                actual = canonical_pair(connection, json.dumps(value), ascii_only)
                expected = json.dumps(
                    value,
                    ensure_ascii=ascii_only,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                assert actual == ("ok", expected)
                assert actual[1].encode("utf8") == expected.encode("utf8")
            for raw in ("1.0", "-0.0", "1.5", '{"a":1.0}', "[1.5]"):
                assert canonical_pair(connection, raw, ascii_only) == ("error", "22023")
        for depth in (-1, 23, 24, 25, None):
            for value in (None, {}, [], {"": None}, [False], {"é": []}):
                canonical_pair(connection, json.dumps(value), depth=depth)
        for kwargs in ({}, {"ascii_only": None}, {"depth": None}):
            assert canonical_pair(connection, None, **kwargs) == ("ok", None)
        assert canonical_pair(connection, "{}", ascii_only=None) == ("ok", None)
        nested = None
        for _ in range(24):
            nested = [nested]
        assert canonical_pair(connection, json.dumps(nested))[0] == "ok"
        assert canonical_pair(connection, json.dumps([nested])) == ("error", "22023")


def test_canonical_batched_byte_boundaries(validation_db):
    with validation_db.begin() as connection:
        for value, valid in (
            ("x" * (LIMIT - 2), True),
            ("x" * (LIMIT - 1), False),
            (["x" * (LIMIT - 4)], True),
            (["x" * (LIMIT - 3)], False),
            ({"a": "x" * (LIMIT - 8)}, True),
            ({"a": "x" * (LIMIT - 7)}, False),
            ({"x" * (LIMIT - 9): None}, True),
            ({"x" * (LIMIT - 8): None}, False),
            (["\\" * (LIMIT // 2)], False),
            ({"\\" * (LIMIT // 2): None}, False),
            (["x" * (LIMIT + 1)], False),
            ({"x" * (LIMIT + 1): None}, False),
        ):
            actual = canonical_pair(connection, json.dumps(value))
            assert actual[0] == ("ok" if valid else "error")
        value = ["😀" * (LIMIT // 12 + 1)]
        assert canonical_pair(connection, json.dumps(value), True) == ("error", "22023")
        assert canonical_pair(connection, json.dumps(value), False)[0] == "ok"


def test_schema_union_branch_recovery_and_malformed_rules(validation_db):
    invalid_date = {"type": "string", "format": "date"}
    string = {"type": "string"}
    with validation_db.begin() as connection:
        # Install and exercise the shared helper too, so equal missing-function
        # errors cannot masquerade as schema parity for unconstrained objects.
        assert schema_pair(
            connection,
            {"nested": [1.25, True, None, {"text": "é"}]},
            {"type": "object", "additionalProperties": True},
        ) == ("ok", True)
        for union in ("anyOf", "oneOf"):
            for branches in ([invalid_date, string], [string, invalid_date]):
                assert schema_pair(connection, "2024-02-30", {union: branches}) == (
                    "ok",
                    True,
                )
            for kind, value in (
                ("array", ["2024-02-30"]),
                ("object", {"date": "2024-02-30"}),
            ):

                def nested(rule, kind=kind):
                    return {
                        "type": kind,
                        **(
                            {"items": rule}
                            if kind == "array"
                            else {"properties": {"date": rule}}
                        ),
                    }

                assert schema_pair(
                    connection, value, {union: [nested(invalid_date), nested(string)]}
                ) == ("ok", True)
            assert schema_pair(connection, "x", {union: [string, string]}) == (
                "ok",
                union == "anyOf",
            )
        for rule in (
            None,
            {},
            {"$ref": "#/$defs/missing"},
            {"anyOf": 1},
            {"type": "string", "minLength": "bad"},
            {"type": "string", "pattern": "["},
            {"type": "string", "enum": 1},
        ):
            assert schema_pair(connection, "x", rule) == ("ok", False)
        for depth in (-1, 23, 24, 25, None):
            schema_pair(connection, "x", string, depth=depth)
            schema_pair(
                connection, ["x"], {"type": "array", "items": string}, depth=depth
            )
            schema_pair(
                connection, "x", {"$ref": "#/$defs/s"}, {"s": string}, depth=depth
            )
        assert schema_pair(connection, None, {"type": "null"}) == ("ok", True)
        assert schema_pair(connection, None, {"type": "null"}, sql_null=True) == (
            "ok",
            False,
        )
        # SQL NULL is distinct from a jsonb null token in each argument.
        for depth in (0, None):
            for rule in (string, {"$ref": "#/$defs/s"}):
                for null_value in (False, True):
                    for null_rule in (False, True):
                        for null_defs in (False, True):
                            actual = schema_pair(
                                connection,
                                "x",
                                rule,
                                {"s": string},
                                depth=depth,
                                sql_null=null_value,
                                sql_null_rule=null_rule,
                                sql_null_defs=null_defs,
                            )
                            expected = not (
                                null_value
                                or null_rule
                                or (null_defs and "$ref" in rule)
                            )
                            assert actual == ("ok", expected)
                # Explicit JSON null definitions are not silently replaced by {}.
                schema_pair(connection, "x", rule, None, depth=depth)
        for value, rule in (
            (True, {"type": "boolean"}),
            (1.5, {"type": "number"}),
            (False, {"type": "integer"}),
            (-1, {"type": "integer"}),
            (1, {"type": "integer", "maximum": "bad"}),
        ):
            schema_pair(connection, value, rule)


def config_corpus():
    from backend.app.analysis_process_ddl import _require_serialized_keys
    from backend.app.rdagent_controller_ddl import _schema
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from tests.rdagent.test_rdagent_workflow_child_contracts import config
    from tests.research_workflow.test_plan import plan

    for cls, instance in (
        (ProviderFactorResearchConfig, plan().research_template),
        (WorkflowFactorResearchConfig, config()),
    ):
        rule = _schema(cls)
        value = instance.model_dump(mode="json")
        for key in ("controller_profile", "workflow_prompt_layout"):
            rule["properties"].pop(key, None)
            value.pop(key, None)
        _require_serialized_keys(rule)
        if cls is ProviderFactorResearchConfig:
            rule["title"] = "FactorResearchConfig"
        yield value, rule


def test_full_config_structurals_and_negative_mutations(validation_db):
    with validation_db.begin() as connection:
        for value, rule in config_corpus():
            defs = rule.get("$defs", {})
            assert schema_pair(connection, value, rule, defs) == ("ok", True)
            mutations = [
                (("input", "window", "start"), "2024-02-30"),
                (("model_binding", "embedding_dimensions"), False),
                (("task", "name"), "bad\nname"),
                (("input", "market_timezone"), "Not/A_Zone"),
                (("input", "calculation_universe", "listing_ids"), ["B", "A"]),
            ]
            for path, replacement in mutations:
                changed = copy.deepcopy(value)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                assert schema_pair(connection, changed, rule, defs) == ("ok", False)
            for changed in (
                {**value, "unknown": 1},
                {k: v for k, v in value.items() if k != "input"},
            ):
                assert schema_pair(connection, changed, rule, defs) == ("ok", False)


@pytest.mark.skipif(
    os.environ.get("MINEAGENT_SQL_PERF_PROBE") != "1",
    reason="opt-in diagnostic, no timing acceptance assertion",
)
def test_nonconstant_server_loop_probe(validation_db):
    with validation_db.begin() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout='60s'")
        for name in ("old_rd_canonical_json", "mineagent_rd_canonical_json"):
            # Each iteration's argument changes, so IMMUTABLE constant folding cannot hide work.
            plan = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, FORMAT JSON) SELECT sum(octet_length({name}("
                "jsonb_build_object('i', i, 'payload', jsonb_build_array(i, 'text', true)), true))) "
                "FROM generate_series(1, 2000) i"
            ).scalar_one()
            print(name, plan)
        for name in ("old_rd_schema", "mineagent_rd_schema"):
            plan = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, FORMAT JSON) SELECT count(*) FILTER (WHERE {name}("
                "jsonb_build_object('i', i, 'values', jsonb_build_array(i, i + 1)), "
                '\'{"type":"object","properties":{"i":{"type":"integer"},'
                '"values":{"type":"array","items":{"type":"integer"}}}}\'::jsonb, '
                "'{}'::jsonb)) FROM generate_series(1, 2000) i"
            ).scalar_one()
            print(name, plan)


def test_frozen_validation_migration_roundtrip_preserves_other_contracts(
    migrated_engine,
):
    """Exercise actual Alembic downgrade/upgrade, then roll back the whole probe."""
    import re
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/alembic"))
    optimized = {"mineagent_rd_canonical_json"}

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
              ('mineagent_rd_canonical_json')
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
            command.downgrade(config, "032d_rd_validation_cost")
            before = state(connection)
            assert_bodies(connection, module.render_upgrade_sql(), schema)
            command.downgrade(config, "032c_oe_provider_table_checks")
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
            command.upgrade(config, "032d_rd_validation_cost")
            assert_bodies(connection, module.render_upgrade_sql(), schema)
            assert state(connection) == before
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).all() == [("032d_rd_validation_cost",)]
        finally:
            transaction.rollback()
