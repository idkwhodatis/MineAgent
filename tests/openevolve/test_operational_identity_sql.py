"""Offline shape/differential corpus. SQL execution is a separate real PG gate."""

import copy
import json

import pytest

from backend.app.openevolve_identity_sql import expanded_spec_schema, identity_sql
from backend.app.openevolve_persistence_ddl import domain_sql, tables
from packages.openevolve_adapter.contracts import SearchSpec
from tests.openevolve.test_contracts import complete_spec

CASES = (
    "valid",
    "missing_default",
    "extra",
    "bool_slots",
    "float_slots",
    "bad_uuid",
    "uppercase_uuid",
    "bad_date",
    "reverse_window",
    "duplicate_windows",
    "wrong_weights",
    "swapped_metrics",
    "mismatched_windows",
    "duplicate_files",
    "zero_calls",
    "zero_tokens",
    "zero_process",
    "seed_unfunded",
    "too_many_slots",
    "zero_slots",
    "big_population",
    "source_substitute",
    "parity_without_marker",
    "bad_sha",
    "wrong_engine",
)


def case_value(case):
    value = complete_spec().model_dump(mode="json")
    if case == "missing_default":
        del value["model_profile"]
    elif case == "extra":
        value["untrusted"] = True
    elif case == "bool_slots":
        value["maximum_mutation_slots"] = True
    elif case == "float_slots":
        value["maximum_mutation_slots"] = 1.0
    elif case == "bad_uuid":
        value["owner_id"] = "not-a-uuid"
    elif case == "uppercase_uuid":
        value["owner_id"] = value["owner_id"].upper()
    elif case == "bad_date":
        value["objective"]["windows"][0]["start"] = "2024-02-30"
    elif case == "reverse_window":
        value["objective"]["windows"][0]["start"] = "2025-01-01"
    elif case == "duplicate_windows":
        value["objective"]["windows"] *= 2
        value["execution"]["windows"] = copy.deepcopy(value["objective"]["windows"])
    elif case == "wrong_weights":
        value["objective"]["windows"][0]["weight_millionths"] = 1
        value["execution"]["windows"] = copy.deepcopy(value["objective"]["windows"])
    elif case == "swapped_metrics":
        value["objective"]["metric_paths"].reverse()
    elif case == "mismatched_windows":
        value["execution"]["windows"][0]["end"] = "2025-01-01"
    elif case == "duplicate_files":
        value["execution"]["files"] *= 2
    elif case == "zero_calls":
        value["budget"]["calls"] = 0
    elif case == "zero_tokens":
        value["budget"]["tokens"] = 0
    elif case == "zero_process":
        value["budget"]["process_slots"] = 0
    elif case == "seed_unfunded":
        value["budget"]["evaluator_executions"] = 0
    elif case == "too_many_slots":
        value["maximum_mutation_slots"] = 3
        value["budget"]["mutation_slots"] = 3
    elif case == "zero_slots":
        value["maximum_mutation_slots"] = 0
    elif case == "big_population":
        value["population_size"] = 9
    elif case == "source_substitute":
        value["preparation"]["prepared"]["source_sha256"] = "f" * 64
    elif case == "parity_without_marker":
        value["preparation"]["parity_profile"] = "native_marker_parity.v1"
    elif case == "bad_sha":
        value["runtime_lock_sha256"] = "g" * 64
    elif case == "wrong_engine":
        value["engine"] = "legacy"
    elif case != "valid":
        raise ValueError(case)
    return value


def expected(value):
    try:
        spec = SearchSpec.model_validate_json(json.dumps(value))
        return (
            spec.model_dump(mode="json") == value
            and spec.preparation.kind == "none"
            and spec.factors.kind == "none"
            and spec.maximum_mutation_slots <= 2
            and spec.population_size <= 8
            and spec.archive_size <= 8
        )
    except ValueError:
        return False


@pytest.mark.parametrize("case", CASES)
def test_corpus_explicit_initial_capability(case):
    assert expected(case_value(case)) == (case == "valid")


def test_schema_expands_only_reviewed_finite_keywords():
    schema = expanded_spec_schema()
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert set(schema["required"]) == set(schema["properties"])
    allowed = {
        "type",
        "additionalProperties",
        "properties",
        "required",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "anyOf",
        "oneOf",
        "enum",
        "const",
    }

    def visit(node):
        assert set(node) <= allowed
        for value in node.get("properties", {}).values():
            visit(value)
        if "items" in node:
            visit(node["items"])
        for branch in node.get("anyOf", []) + node.get("oneOf", []):
            visit(branch)

    visit(schema)


def test_domain_exact32_and_all_deferred_fk_no_weak_projection():
    sql = domain_sql("oe_test")
    assert len(tables()) == 32
    assert sql.count("CREATE TABLE oe_test.") == 32
    assert sql.count("CREATE TRIGGER oe024_immutable") == 32
    assert sql.count("CREATE TRIGGER oe024_run_lock") == 32
    assert sql.count("FOREIGN KEY") == sql.count("DEFERRABLE INITIALLY DEFERRED")
    assert "FOR UPDATE NOWAIT" in sql
    assert "DROP CONSTRAINT" not in sql and "DROP TRIGGER" not in sql


@pytest.mark.parametrize(
    "name", ["public;DROP SCHEMA public", "../foo", 'x"', "", None]
)
def test_schema_injection_rejected(name):
    for renderer in (domain_sql, identity_sql):
        with pytest.raises(ValueError):
            renderer(name)
