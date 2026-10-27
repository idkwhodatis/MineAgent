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


def extended_canonical_cases(tmp_path):
    """Same 427-value corpus as the independently executed rollback PG probe."""
    import random

    from packages.rdagent_adapter.provider_prompt import first_provider_messages
    from tests.rdagent.test_rdagent_controller_process import records
    from tests.rdagent.test_rdagent_controller_services import fixture
    from tests.rdagent_provider.test_steer_prompt import configuration

    values = [
        None,
        True,
        False,
        0,
        -1,
        9223372036854775807,
        "",
        "ASCII text",
        'a"b',
        "a\\b",
        "/",
        "\b\t\n\f\r",
        "".join(map(chr, range(1, 32))),
        "".join(map(chr, range(32, 127))),
        "\x7f",
        "é中文😀\u2028\u2029",
        {"z": "x", "a": ["x", 1, True, None]},
        "a" * 8192,
    ]
    values += CANONICAL_CASES
    values += [chr(i) for i in range(1, 256)]
    rng = random.Random(2601027)
    alphabet = (
        "abcXYZ09 "
        + chr(34)
        + chr(92)
        + "\n\t"
        + "".join(chr(i) for i in [127, 128, 233, 0x4E2D, 0x1F600, 0x10FFFF])
    )

    def random_value(depth=0):
        choices = [
            None,
            bool(rng.randrange(2)),
            rng.randrange(-(2**53), 2**53),
            "".join(rng.choice(alphabet) for _ in range(rng.randrange(80))),
        ]
        if depth < 3:
            choices += [
                [random_value(depth + 1) for _ in range(rng.randrange(4))],
                {
                    str(k) + rng.choice(alphabet): random_value(depth + 1)
                    for k in range(rng.randrange(4))
                },
            ]
        return rng.choice(choices)

    values += [random_value() for _ in range(128)]
    values += [
        first_provider_messages(configuration()),
        first_provider_messages(configuration('untrusted 中文😀 " quoted')),
    ]
    values.append(fixture()[2].model_dump(mode="json"))
    values += [value.model_dump(mode="json") for value in records(tmp_path)]
    assert len(values) == 427
    return values


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
    import hashlib

    branch = (
        "   IF t COLLATE \"C\" !~ '[^ -~]' AND position(chr(34) in t)=0 AND position(chr(92) in t)=0 THEN\n"
        "     result:=chr(34)||t||chr(34);\n"
        "   ELSE\n"
    )
    assert sql.count(branch) == 1
    original = sql.replace(branch, "", 1).replace(
        "WITH ORDINALITY AS chars(c,ord) WHERE chars.c<>'') z;\n   END IF;",
        "WITH ORDINALITY AS chars(c,ord) WHERE chars.c<>'') z;",
        1,
    )
    assert (
        hashlib.sha256(original.encode()).hexdigest()
        == "5aa2486bbd5cd7b2b6883a6e57ae56166f5db7134430c96480c3d92479c4eca8"
    )


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


def test_reference_special_cases_are_not_approximate_escaping(tmp_path):
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

    # Safe subset is a strict optimization, never an alternate escaping policy.
    for value in extended_canonical_cases(tmp_path):
        for ascii_only in (True, False):
            encoded = json.dumps(
                value,
                ensure_ascii=ascii_only,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            assert json.loads(encoded) == json.loads(json.dumps(value))
    for code in range(1, 256):
        value = chr(code)
        safe = 32 <= code <= 126 and value not in ('"', "\\")
        if safe:
            for ascii_only in (True, False):
                assert '"' + value + '"' == json.dumps(value, ensure_ascii=ascii_only)

    # Every installed historical definition must preserve renderer parity;
    # no later upgrade or downgrade may overwrite the algorithm with old bytes.
    definitions = []
    import re

    root = Path(__file__).resolve().parents[2]
    for path in sorted((root / "backend/alembic/versions").glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.Assign):
                continue
            try:
                literal = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if not isinstance(literal, str):
                continue
            for match in re.finditer(
                r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?mineagent_rd_canonical_json\(",
                literal,
            ):
                definition = literal[
                    match.start() : literal.index("$$;", match.start()) + 3
                ]
                assert (
                    definition
                    == render_sql("public").replace("public.", "@SCHEMA@.").strip()
                )
                definitions.append((path.name, node.targets[0].id))
    assert definitions == [("018_rd_source_persistence.py", "UPGRADE_SQL")]
