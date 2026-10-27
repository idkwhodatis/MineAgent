"""Offline renderer checks; actual SQL equivalence belongs to the PG test file."""

import ast
import json
from pathlib import Path

import pytest

from backend.app.rdagent_canonical_sql import MAX_BYTES, MAX_DEPTH, render_sql

# Fixed portable corpus, also consumed by the PostgreSQL differential test.
CANONICAL_CASES = [
    None,
    True,
    False,
    0,
    -1,
    9007199254740991,
    -9007199254740991,
    "",
    "plain",
    '"\\/',
    "\b\t\n\f\r",
    "".join(chr(i) for i in range(1, 32)),
    "\x7f\x80é中文\u2028\u2029\uffff",
    "😀𐀀\U0010ffff",
    [],
    {},
    [None, False, True, 12, -12, "x"],
    {"z": 1, "a": [2, {"中文": "😀"}], "A": 3, "ä": 4},
    {"😀": 0, "\ue000": 1, "𐀀": 2, "a": 3, "\x01": 4},
]


def test_renderer_only_adds_one_schemaqualified_strict_function():
    sql = render_sql("rd_canon")
    assert sql == render_sql("rd_canon")
    assert sql.count("CREATE OR REPLACE FUNCTION") == 1
    assert "rd_canon.mineagent_rd_canonical_json(" in sql
    assert "@S@" not in sql
    assert "IMMUTABLE STRICT" in sql
    assert "SET search_path=pg_catalog" in sql
    assert 'ORDER BY key COLLATE "C"' in sql
    assert "CREATE TRIGGER" not in sql
    assert "mineagent_neutral_" not in sql
    assert "current_setting('server_encoding')" in sql

    # Exercise each real fixture's exact DDL call through SQLAlchemy's DBAPI
    # dispatch, then psycopg's actual parser; no server/DDL execution is claimed.
    from psycopg import ProgrammingError
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer
    from sqlalchemy import create_engine, event

    from backend.app.rdagent_identity_sql import render_sql as identity_render

    root = Path(__file__).resolve().parents[2]
    cases = (
        ("rdagent_canonical", "render_sql", render_sql),
        ("rdagent_identity", "render_sql", identity_render),
    )
    # OE identity now consumes migration-installed functions; it must never be
    # included in standalone DDL replay or overwrite the installed predicates.
    oe_fixture = ast.parse(
        (root / "tests/integration/test_openevolve_identity_postgres.py").read_text()
    )
    assert not any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "exec_driver_sql"
            or isinstance(node.func, ast.Name)
            and node.func.id == "identity_sql"
        )
        for node in ast.walk(oe_fixture)
    )
    # The pre-repair default passed an empty parameter collection, causing
    # psycopg to parse literal %1024 as a placeholder even with no bind values.
    query = PostgresQuery(Transformer())
    with pytest.raises(ProgrammingError, match="only.*allowed"):
        query.convert(render_sql("rd_canon"), ())
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            received = []

            def original_dispatch(cursor, statement, parameters, context):
                assert context.no_parameters is False and parameters == ()
                PostgresQuery(Transformer()).convert(statement, parameters)
                pytest.fail("Original literal modulo unexpectedly parsed")

            event.listen(engine, "do_execute", original_dispatch)
            try:
                with pytest.raises(ProgrammingError, match="only.*allowed"):
                    connection.exec_driver_sql(render_sql("rd_canon"))
            finally:
                event.remove(engine, "do_execute", original_dispatch)

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                parsed = PostgresQuery(Transformer())
                parsed.convert(statement, None)
                assert parsed.query == statement.encode("utf-8")
                received.append(statement)
                return True

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                pytest.fail("Fixture literal DDL entered parameterized execution")

            for name, renderer_name, renderer in cases:
                path = root / f"tests/integration/test_{name}_postgres.py"
                calls = [
                    node
                    for node in ast.walk(ast.parse(path.read_text()))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "exec_driver_sql"
                ]
                assert len(calls) == 1
                expression = ast.Expression(calls[0])
                eval(
                    compile(expression, str(path), "eval"),
                    {
                        "connection": connection,
                        "schema": "rd_canon",
                        renderer_name: renderer,
                    },
                )
                assert received[-1] == renderer("rd_canon")
            assert len(received) == len(cases)
    finally:
        engine.dispose()


@pytest.mark.parametrize("schema", ["", "a.b", "Mixed", "x;drop", "a" * 64])
def test_schema_closed(schema):
    with pytest.raises(ValueError):
        render_sql(schema)


def test_bounds_integer_only_and_surrogate_arithmetic_explicit():
    sql = render_sql()
    assert f"depth>{MAX_DEPTH}" in sql
    assert f"octet_length(result)>{MAX_BYTES}" in sql
    assert "'^(0|-?[1-9][0-9]*)$'" in sql
    assert "55296+(z.p-65536)/1024" in sql
    assert "56320+(z.p-65536)%1024" in sql
    assert "p>=127" in sql
    assert "ERRCODE='22023'" in sql


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("value", CANONICAL_CASES)
def test_reference_corpus_python_roundtrip(value, ascii_only):
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ascii_only,
        allow_nan=False,
    )
    assert json.loads(raw) == value
    assert len(raw.encode()) <= MAX_BYTES


def test_reference_special_cases_are_not_approximate_escaping():
    assert json.dumps("\x7f", ensure_ascii=True) == '"\\u007f"'
    assert json.dumps("😀", ensure_ascii=True) == '"\\ud83d\\ude00"'
    assert json.dumps("\b", ensure_ascii=True) == '"\\b"'
    assert json.dumps("/", ensure_ascii=True) == '"/"'
    assert (
        json.dumps(
            {"😀": 0, "\ue000": 1},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        == '{"\ue000":1,"😀":0}'
    )
