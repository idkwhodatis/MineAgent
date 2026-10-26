"""Offline renderer checks; actual SQL equivalence belongs to the PG test file."""

import json

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
