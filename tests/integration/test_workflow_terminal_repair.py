"""Actual PostgreSQL relational predicate, not a synthetic successful engine run."""

import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.postgres


def assert_schema_dependencies_resolve_without_exception_masks(connection):
    from workflow_terminal_repair_fixture import SCHEMA, dependencies

    # Direct calls expose missing helpers instead of the checkpoint/raw
    # predicates converting an undefined_function exception into false.
    union = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    cases = (
        ("value", union, True),
        (True, union, False),
        ({"x": 1}, {"type": "object", "properties": {"x": union}}, True),
        ({"x": True}, {"type": "object", "properties": {"x": union}}, False),
        (["value"], {"type": "array", "items": union}, True),
        ([False], {"type": "array", "items": union}, False),
        (
            {"x": [1, "value"]},
            {"type": "object", "additionalProperties": True},
            True,
        ),
    )
    transaction = connection.begin_nested()
    try:
        connection.exec_driver_sql(f"CREATE SCHEMA {SCHEMA}")
        for statement in dependencies():
            connection.exec_driver_sql(
                statement, execution_options={"no_parameters": True}
            )
        for depth in (0, -1, None):
            for value, rule, expected in cases:
                assert (
                    connection.scalar(
                        text(
                            f"SELECT {SCHEMA}.mineagent_rd_schema("
                            "CAST(:value AS jsonb), CAST(:rule AS jsonb),"
                            " '{}'::jsonb, :depth)"
                        ),
                        {
                            "value": json.dumps(value),
                            "rule": json.dumps(rule),
                            "depth": depth,
                        },
                    )
                    is expected
                )
    finally:
        transaction.rollback()


def test_terminal_repair_actual_predicate_and_reversible_maintenance(migrated_engine):
    from workflow_terminal_repair_fixture import statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert_schema_dependencies_resolve_without_exception_masks(connection)
            for statement in statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "TERMINAL032A_MATRIX_COMPLETE"
        finally:
            transaction.rollback()
