"""Schema metadata supplement checks; no live PostgreSQL or safety proof."""

import json

import pytest

from script import heartbeat_schema_inventory as m
from tests.sql_probes.test_heartbeat_graph_inventory_source import SCHEMA, fixture_rows


def rows():
    trigger = fixture_rows()[0][0] | {"relation": "execution_attempts"}
    constraint = {
        "relation": "execution_attempts",
        "name": "attempt_run_fk",
        "kind": "f",
        "deferrable": False,
        "initially_deferred": False,
        "validated": True,
        "columns": "{1,2}",
        "referenced_columns": "{2,3}",
        "referenced_relation": "research_runs",
        "referenced_schema": SCHEMA,
        "update_action": "a",
        "delete_action": "r",
        "match_type": "s",
        "definition": "PRIVATE CHECK EXPRESSION",
    }
    column = {
        "relation": "execution_attempts",
        "ordinal": 1,
        "name": "run_id",
        "not_null": True,
        "dropped": False,
    }
    return [trigger], [constraint], [column]


def test_supplement_retains_bindings_coverage_and_only_hashes_expressions():
    triggers, constraints, columns = rows()
    triggers[0]["predicate"] = (
        "PRIVATE CREATE TRIGGER WHEN (OLD.run_id IS DISTINCT FROM NEW.run_id)"
    )
    triggers[0]["definition"] = triggers[0]["predicate"]
    result = m.summarize(SCHEMA, triggers, constraints, columns)
    assert "PRIVATE" not in json.dumps(result) and SCHEMA not in json.dumps(result)
    assert result["schema_triggers"][0]["relation"] == "execution_attempts"
    assert result["schema_triggers"][0]["predicate_sha256"] == m.digest(
        triggers[0]["predicate"], SCHEMA
    )
    assert (
        result["schema_triggers"][0]["predicate_encoding"] == "full_trigger_definition"
    )
    assert (
        result["schema_triggers"][0]["predicate_sha256"]
        == result["schema_triggers"][0]["definition_sha256"]
    )
    assert result["schema_triggers"][0]["event_bits"] == 29
    assert result["schema_constraints"][0]["columns"] == "{1,2}"
    assert result["schema_constraints"][0]["referenced_relation"] == "research_runs"
    assert result["schema_constraints"][0]["validated"]
    assert result["schema_columns"][0]["name"] == "run_id"
    constraints[0].update(
        kind="c",
        referenced_relation=None,
        referenced_schema=None,
        referenced_columns=None,
        update_action=" ",
        delete_action=" ",
        match_type=" ",
    )
    assert (
        m.summarize(SCHEMA, triggers, constraints, columns)["schema_constraints"][0][
            "referenced_relation"
        ]
        is None
    )


def test_bad_names_namespaces_and_unbounded_metadata_rejected():
    for group, key, value in (
        (0, "relation", "private; SQL"),
        (0, "function_schema", "other_schema"),
        (0, "enabled", "X"),
        (0, "event_bits", -1),
        (1, "referenced_schema", "other_schema"),
        (1, "columns", "secret"),
        (1, "kind", "z"),
        (1, "delete_action", "secret"),
        (2, "ordinal", 0),
        (2, "name", "private; SQL"),
    ):
        data = rows()
        data[group][0][key] = value
        with pytest.raises(ValueError):
            m.summarize(SCHEMA, *data)
    for group in range(3):
        data = list(rows())
        data[group] *= m.MAX_ROWS + 1
        with pytest.raises(ValueError, match="Bounded"):
            m.summarize(SCHEMA, *data)
    with pytest.raises(ValueError, match="disposable"):
        m.summarize("public", *rows())


def test_all_queries_are_schema_scoped_bounded_catalog_only():
    from script import heartbeat_graph_inventory as original

    assert "pg_get_expr" not in original.TRIGGERS_QUERY
    assert (
        "CASE WHEN t.tgqual IS NOT NULL THEN pg_get_triggerdef(t.oid) END AS predicate"
        in original.TRIGGERS_QUERY
    )
    for query in (m.TRIGGERS_QUERY, m.CONSTRAINTS_QUERY, m.COLUMNS_QUERY):
        assert query.startswith("SELECT ")
        assert "n.nspname=current_schema()" in query
        assert "LIMIT 4097" in query
        assert "FROM pg_catalog." in query
    assert "NOT t.tgisinternal" in m.TRIGGERS_QUERY
    assert "pg_get_expr" not in m.TRIGGERS_QUERY
    assert (
        "CASE WHEN t.tgqual IS NOT NULL THEN pg_get_triggerdef(t.oid) END AS predicate"
        in m.TRIGGERS_QUERY
    )
    assert "convalidated" in m.CONSTRAINTS_QUERY
    assert "attnum" in m.COLUMNS_QUERY


def test_read_only_checked_before_schema_queries():
    class Connection:
        def exec_driver_sql(self, sql):
            assert sql == "SHOW transaction_read_only"
            return self

        def scalar_one(self):
            return "off"

    with pytest.raises(ValueError, match="Read-only"):
        m.capture(Connection(), SCHEMA)


def test_capture_adds_explicit_version_without_removing_original_inventory(monkeypatch):
    from script import heartbeat_graph_inventory as original

    triggers, functions = fixture_rows()
    data = {
        "SHOW transaction_read_only": "on",
        "SELECT current_schema()": SCHEMA,
        "SHOW server_version_num": "180006",
        "SELECT version_num FROM alembic_version": [original.REVISION],
        original.TRIGGERS_QUERY: triggers,
        original.FUNCTIONS_QUERY: functions,
        **dict(
            zip(
                (m.TRIGGERS_QUERY, m.CONSTRAINTS_QUERY, m.COLUMNS_QUERY),
                rows(),
                strict=True,
            )
        ),
    }

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return self.value

        def mappings(self):
            return self.value

    class Connection:
        def exec_driver_sql(self, sql):
            return Result(data[sql])

    result = original.capture(Connection())
    assert result["schema_version"] == m.VERSION
    assert (
        result["triggers"]
        == original.summarize(SCHEMA, triggers, functions)["triggers"]
    )
    assert (
        result["functions"]
        == original.summarize(SCHEMA, triggers, functions)["functions"]
    )
    assert set(result) >= {"schema_triggers", "schema_constraints", "schema_columns"}
