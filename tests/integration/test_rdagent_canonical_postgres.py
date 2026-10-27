"""Actual PostgreSQL/Python canonical-byte differential; never SQLite fallback."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.rdagent_canonical_sql import MAX_BYTES, render_sql

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_rd_canonical_postgres_corpus",
    ROOT / "tests/rdagent/test_rdagent_canonical_sql.py",
)
CORPUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORPUS)


@pytest.fixture
def canonical_db(migrated_engine):
    with migrated_engine.begin() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        installed = connection.execute(
            text("""
            SELECT p.prosrc FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=current_schema()
              AND p.proname='mineagent_rd_canonical_json'
        """)
        ).scalar_one()
        assert "IF t COLLATE \"C\" !~ '[^ -~]'" in installed
        assert "position(chr(34) in t)=0 AND position(chr(92) in t)=0" in installed
        assert (
            "ELSIF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN"
            in installed
        )
        assert "result:=pg_catalog.to_json(t)::text;" in installed
        connection.exec_driver_sql(
            render_sql(schema), execution_options={"no_parameters": True}
        )
    return migrated_engine


def encode(engine, value, ascii_only=True, depth=0):
    with engine.begin() as connection:
        return connection.execute(
            text("""
            SELECT mineagent_rd_canonical_json(CAST(:v AS jsonb), :ascii, :depth)
        """),
            {
                "v": json.dumps(value, allow_nan=False),
                "ascii": ascii_only,
                "depth": depth,
            },
        ).scalar_one()


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("value", CORPUS.CANONICAL_CASES)
def test_exact_python_canonical_bytes_and_sha256(canonical_db, value, ascii_only):
    expected = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ascii_only,
        allow_nan=False,
    )
    actual = encode(canonical_db, value, ascii_only)
    assert actual == expected
    assert actual.encode("utf-8") == expected.encode("utf-8")
    assert (
        hashlib.sha256(actual.encode()).digest()
        == hashlib.sha256(expected.encode()).digest()
    )


@pytest.mark.parametrize("value", [1.0, 1.5, -0.0, -1.5, {"n": 1.0}, [1.5]])
def test_fractional_or_float_tokens_fail_closed(canonical_db, value):
    with pytest.raises(DBAPIError) as exc:
        encode(canonical_db, value)
    assert exc.value.orig.sqlstate == "22023"


@pytest.mark.parametrize("depth", [-1, 25])
def test_invalid_caller_depth_rejected(canonical_db, depth):
    with pytest.raises(DBAPIError) as exc:
        encode(canonical_db, None, depth=depth)
    assert exc.value.orig.sqlstate == "22023"


def test_nested_depth_boundary(canonical_db):
    value = None
    for _ in range(24):
        value = [value]
    assert encode(canonical_db, value) == json.dumps(value, separators=(",", ":"))
    with pytest.raises(DBAPIError) as exc:
        encode(canonical_db, [value])
    assert exc.value.orig.sqlstate == "22023"


def test_exact_output_byte_boundary(canonical_db):
    value = "x" * (MAX_BYTES - 2)
    assert len(encode(canonical_db, value).encode()) == MAX_BYTES
    with pytest.raises(DBAPIError) as exc:
        encode(canonical_db, value + "x")
    assert exc.value.orig.sqlstate == "22023"

    # C string escaping may expand input; enforce the same final byte bound.
    for character, maximum in (
        (chr(1), 174762),
        ('"', 524287),
        ("\\", 524287),
        ("\n", 524287),
    ):
        for ascii_only in (True, False):
            accepted = character * maximum
            expected = json.dumps(accepted, ensure_ascii=ascii_only)
            assert encode(canonical_db, accepted, ascii_only) == expected
            assert len(expected.encode()) <= MAX_BYTES
            with pytest.raises(DBAPIError) as exc:
                encode(canonical_db, accepted + character, ascii_only)
            assert exc.value.orig.sqlstate == "22023"


def test_ascii_escape_expansion_counts_output_not_input(canonical_db):
    value = "😀" * (MAX_BYTES // 12 + 1)
    with pytest.raises(DBAPIError) as exc:
        encode(canonical_db, value, True)
    assert exc.value.orig.sqlstate == "22023"
    assert encode(canonical_db, value, False) == json.dumps(value, ensure_ascii=False)


def test_sql_null_strictness_distinct_from_json_null(canonical_db):
    with canonical_db.begin() as connection:
        assert (
            connection.execute(
                text("""
            SELECT mineagent_rd_canonical_json(NULL::jsonb,true)
        """)
            ).scalar_one()
            is None
        )
        assert (
            connection.execute(
                text("""
            SELECT mineagent_rd_canonical_json('null'::jsonb,true)
        """)
            ).scalar_one()
            == "null"
        )
        assert (
            connection.execute(
                text("""
            SELECT mineagent_rd_canonical_json('{}'::jsonb,NULL::boolean)
        """)
            ).scalar_one()
            is None
        )


def test_jsonb_lexical_loss_is_explicit_not_raw_validation(canonical_db, tmp_path):
    with canonical_db.begin() as connection:
        # This demonstrates why the serializer cannot replace raw codec parsing.
        assert (
            connection.execute(
                text("""
            SELECT mineagent_rd_canonical_json(CAST(:v AS jsonb),true)
        """),
                {"v": '{"x":1,"x":2,"n":1e0}'},
            ).scalar_one()
            == '{"n":1,"x":2}'
        )

    # Bind exact UTF-8 expectations as hex: SQL script transports must not
    # normalize U+0085/U+2028/U+2029 into line boundaries.
    vectors = [
        {
            "value": value,
            "ascii": ascii_only,
            "expected_hex": json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=ascii_only,
                allow_nan=False,
            )
            .encode()
            .hex(),
        }
        for value in CORPUS.escaped_canonical_cases(tmp_path)
        for ascii_only in (True, False)
    ]
    with canonical_db.begin() as connection:
        results = (
            connection.execute(
                text("""
            SELECT encode(convert_to(mineagent_rd_canonical_json(
                v->'value',(v->>'ascii')::boolean),'UTF8'),'hex') = v->>'expected_hex'
            FROM jsonb_array_elements(CAST(:vectors AS jsonb)) AS v
        """),
                {"vectors": json.dumps(vectors, ensure_ascii=True)},
            )
            .scalars()
            .all()
        )
    assert len(results) == 3278 and all(value is True for value in results)

    # JSONB itself rejects invalid Unicode before either serializer can run.
    for raw in ('"\\ud800"', '"\\udfff"', '"\\u0000"', '"\\ud800a"'):
        with pytest.raises(DBAPIError) as exc, canonical_db.begin() as connection:
            connection.execute(
                text("SELECT mineagent_rd_canonical_json(CAST(:v AS jsonb),true)"),
                {"v": raw},
            )
        assert exc.value.orig.sqlstate in {"22P02", "22P05"}
