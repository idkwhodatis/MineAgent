"""Real migrated PostgreSQL predicate differential; collection is not acceptance.

Uses the functions actually installed by the full migration chain. No fixture
replacement functions, weaker tables, or removed guards.
"""

import json

import pytest
from sqlalchemy import text

from tests.openevolve.test_operational_identity_sql import CASES, case_value, expected

pytestmark = pytest.mark.postgres


@pytest.fixture
def oe_identity_db(migrated_engine):
    # HEAD already installs these functions. Re-running a source CREATE fragment
    # both collides with migration-owned functions and would test the wrong body.
    with migrated_engine.connect() as connection:
        installed = connection.execute(
            text(
                "SELECT bool_and(pg_catalog.to_regprocedure("
                "pg_catalog.format('%I.%s', current_schema(), signature)) IS NOT NULL) "
                "FROM (VALUES "
                "('mineagent_oe_shape(jsonb,jsonb,integer)'), "
                "('mineagent_oe_source_config(jsonb)'), "
                "('mineagent_oe_context(jsonb)'), "
                "('mineagent_oe_request(jsonb,text)')) AS required(signature)"
            )
        ).scalar_one()
        assert installed is True, "Migration-owned OE identity functions are missing"
    return migrated_engine


@pytest.mark.parametrize("case", CASES)
def test_exact_search_config_differential(oe_identity_db, case):
    value = case_value(case)
    with oe_identity_db.connect() as connection:
        result = connection.execute(
            text("SELECT mineagent_oe_source_config(CAST(:v AS jsonb))"),
            {"v": json.dumps(value)},
        ).scalar_one()
    assert result is expected(value)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "bool_iteration",
        "third_iteration",
        "next_operation",
        "missing_default",
        "foreign_schema",
        "uppercase_uuid",
        "extra",
    ],
)
def test_exact_operational_context_differential(oe_identity_db, case):
    from uuid import uuid4

    from packages.openevolve_adapter.contracts import SearchOperationContext

    value = SearchOperationContext(
        owner_id=uuid4(),
        run_id=uuid4(),
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256="c" * 64,
    ).model_dump(mode="json")
    if case == "bool_iteration":
        value["native_iteration"] = True
    elif case == "third_iteration":
        value["native_iteration"] = 3
    elif case == "next_operation":
        value["operation_ordinal"] = 1
    elif case == "missing_default":
        del value["purpose"]
    elif case == "foreign_schema":
        value["schema_version"] = "mineagent.neutral-context.v1"
    elif case == "uppercase_uuid":
        value["owner_id"] = value["owner_id"].upper()
    elif case == "extra":
        value["extra"] = None
    with oe_identity_db.connect() as connection:
        result = connection.execute(
            text("SELECT mineagent_oe_context(CAST(:v AS jsonb))"),
            {"v": json.dumps(value)},
        ).scalar_one()
    assert result is (case == "valid")


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "unicode",
        "bool_tokens",
        "float_tokens",
        "wrong_order",
        "one_message",
        "empty_message",
        "extra",
        "wrong_protocol",
        "utf8_overbound",
    ],
)
def test_exact_operational_request_differential(oe_identity_db, case):
    from packages.research_runtime.engine_broker_contracts import OEChatRequest

    value = OEChatRequest(
        messages=(
            {"role": "system", "content": "fixed"},
            {"role": "user", "content": "mutation"},
        )
    ).model_dump(mode="json")
    protocol = "mineagent.openevolve-chat.v1"
    if case == "unicode":
        value["messages"][1]["content"] = "策略 😀"
    elif case == "bool_tokens":
        value["max_output_tokens"] = True
    elif case == "float_tokens":
        value["max_output_tokens"] = 32768.0
    elif case == "wrong_order":
        value["messages"].reverse()
    elif case == "one_message":
        value["messages"].pop()
    elif case == "empty_message":
        value["messages"][0]["content"] = ""
    elif case == "extra":
        value["extra"] = None
    elif case == "wrong_protocol":
        protocol = "mineagent.neutral-model.v1"
    elif case == "utf8_overbound":
        value["messages"][1]["content"] = "😀" * 262144
    with oe_identity_db.connect() as connection:
        result = connection.execute(
            text("SELECT mineagent_oe_request(CAST(:v AS jsonb), :p)"),
            {"v": json.dumps(value), "p": protocol},
        ).scalar_one()
    assert result is (case in {"valid", "unicode"})
