"""Source-only privacy and boundedness checks, not installed catalog evidence."""

import ast
import json
from pathlib import Path

import pytest

from script import heartbeat_graph_inventory as m

SCHEMA = "tradegrad_test_" + "a" * 32


def fixture_rows():
    triggers = [
        {
            "name": "probe_trigger",
            "internal": False,
            "event_bits": 29,
            "enabled": "O",
            "deferrable": True,
            "initially_deferred": True,
            "constraint_trigger": True,
            "update_columns": "",
            "function_schema": SCHEMA,
            "function_name": "probe_function",
            "identity_arguments": "",
            "definition": "PRIVATE SQL",
            "predicate": None,
            "arguments": "PRIVATE ARGS",
        }
    ]
    functions = [
        {
            "name": "probe_function",
            "identity_arguments": "rid uuid",
            "language": "plpgsql",
            "volatility": "v",
            "security_definer": False,
            "strict": False,
            "parallel": "u",
            "kind": "f",
            "leakproof": False,
            "body": "PRIVATE BODY; SELECT *; to_jsonb(OLD); EXECUTE secret; probe_function(); lease_expires_at",
            "definition": "PRIVATE DEFINITION",
            "configuration": "PRIVATE CONFIG",
            "acl": "PRIVATE ACL",
        }
    ]
    return triggers, functions


def test_exact_safe_shape_hashes_and_honest_lexical_observations():
    triggers, functions = fixture_rows()
    result = m.summarize(SCHEMA, triggers, functions)
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded and SCHEMA not in encoded
    assert "static closure NOT established" in result["dependency_status"]
    item = result["functions"][0]
    assert item["lexical_local_references"] == ["probe_function"]
    assert item["lexical_execute_present"]
    assert item["lexical_whole_row_present"]
    assert item["lexical_heartbeat_present"]
    assert item["body_sha256"] == m.digest(functions[0]["body"], SCHEMA)
    assert set(result) == {
        "revision",
        "server_version_num",
        "scope",
        "dependency_status",
        "source_provenance_status",
        "gaps",
        "triggers",
        "functions",
    }
    assert not ({"body", "definition", "configuration", "acl"} & item.keys())
    assert not item["lexical_row_lock_present"]
    original_body = functions[0]["body"]
    for lock in ("FOR UPDATE", "FOR SHARE", "FOR NO KEY UPDATE", "FOR KEY SHARE"):
        functions[0]["body"] = original_body + " -- " + lock
        assert m.summarize(SCHEMA, triggers, functions)["functions"][0][
            "lexical_row_lock_present"
        ]
    functions[0]["body"] = original_body
    functions[0]["body"] += " changed"
    assert (
        m.summarize(SCHEMA, triggers, functions)["functions"][0]["body_sha256"]
        != item["body_sha256"]
    )


def test_schema_normalization_is_exact_and_output_is_bounded():
    assert m.digest(SCHEMA + ".function()", SCHEMA) == m.digest(
        "@SCHEMA@.function()", SCHEMA
    )
    triggers, functions = fixture_rows()
    for schema in ("public", "tradegrad_test_bad", "secret/password"):
        with pytest.raises(ValueError):
            m.summarize(schema, triggers, functions)
    with pytest.raises(ValueError):
        m.summarize(SCHEMA, triggers * (m.MAX_ROWS + 1), functions)
    triggers[0]["function_schema"] = "unreviewed"
    with pytest.raises(ValueError):
        m.summarize(SCHEMA, triggers, functions)


def test_unexpected_identity_cannot_leak_into_artifact():
    triggers, functions = fixture_rows()
    functions[0]["name"] = "private token; SELECT"
    with pytest.raises(ValueError):
        m.summarize(SCHEMA, triggers, functions)
    with pytest.raises(ValueError):
        m.arguments("rid text DEFAULT 'secret'", SCHEMA)


def test_read_only_guard_precedes_every_other_query():
    class Connection:
        def exec_driver_sql(self, sql):
            assert sql == "SHOW transaction_read_only"
            return self

        def scalar_one(self):
            return "off"

    with pytest.raises(ValueError, match="Read-only"):
        m.capture(Connection())


def test_exporter_has_no_mutating_sql_or_application_rows_or_runtime_imports():
    tree = ast.parse(Path(m.__file__).read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert not any(
        n.func.attr in {"execute", "commit", "rollback", "connect", "begin"}
        for n in calls
    )
    for node in calls:
        if node.func.attr == "exec_driver_sql" and isinstance(
            node.args[0], ast.Constant
        ):
            assert node.args[0].value.startswith(("SELECT ", "SHOW "))
    assert "FROM execution_attempts" not in m.TRIGGERS_QUERY
    assert "LIMIT 4097" in m.FUNCTIONS_QUERY and "LIMIT 4097" in m.TRIGGERS_QUERY
    root = Path(m.__file__).resolve().parents[1]
    for path in (root / "backend").rglob("*.py"):
        assert "heartbeat_graph_inventory" not in path.read_text()
