"""Actual psycopg conversion of complete fixture SQL, without any connection."""

import ast
import importlib
from pathlib import Path

from psycopg import ProgrammingError
from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

ROOT = Path(__file__).resolve().parents[2]


def test_generated_literal_matrices_bypass_placeholder_parsing_without_sql_changes(
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    groups = (
        ("declared_upload_sql_fixture", "probe_statements", 35),
        ("declared_upload_sql_fixture", "actual_basket_statements", 2),
        ("workflow_declaration_sql_fixture", "probe_statements", 1),
        ("normal_oe_note_sql_fixture", "identity_dataset_statements", 1),
    )
    for module, function, expected_failures in groups:
        failures = 0
        for statement in getattr(importlib.import_module(module), function)():
            try:
                PostgresQuery(Transformer()).convert(statement, {})
            except ProgrammingError:
                failures += 1

            class Cursor:
                def __init__(self, expected):
                    self.expected = expected

                def execute(self, sql, *parameters):
                    assert sql == self.expected and not parameters
                    converted = PostgresQuery(Transformer())
                    converted.convert(sql, None)
                    assert converted.query == self.expected.encode("utf-8")

            PGDialect_psycopg().do_execute_no_params(Cursor(statement), statement)
        assert failures == expected_failures
    # Verify the actual integration callers select that dialect route, not merely
    # a successful detached demonstration. Bound SQLAlchemy text calls are untouched.
    for name, expected_calls in (
        ("test_declared_upload_persistence.py", 6),
        ("test_rd_workflow_declaration_graph.py", 1),
        ("test_normal_oe_note_persistence.py", 3),
    ):
        tree = ast.parse((ROOT / "tests/integration" / name).read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exec_driver_sql"
        ]
        assert len(calls) == expected_calls
        for call in calls:
            assert len(call.args) == 1
            assert isinstance(call.args[0], ast.Name) and call.args[0].id == "statement"
            assert len(call.keywords) == 1
            assert call.keywords[0].arg == "execution_options"
            assert ast.literal_eval(call.keywords[0].value) == {"no_parameters": True}
