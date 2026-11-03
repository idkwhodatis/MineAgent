"""Explicit test-only disposable032c experiment; never installed by application code.

Only the exact canonical printable-ASCII branch is reused in raw JSON. Quotes,
backslashes, controls, Unicode and larger strings use the original helper. The
1048574-byte input cap preserves its 1048576-byte quoted-output bound. Original
UTF8, depth, duplicate-before-recursion, numeric and raw16MiB guards are intact.
"""

import os
from hashlib import sha256

from sqlalchemy import text

from backend.app.openevolve_revision_ddl import _function, historical
from tests.integration.rd_hash_reuse_experiment import CATALOG, _body, _fixture

ENV = "MINEAGENT_OE_RAW_ASCII_EXPERIMENT"
FUNCTION = "mineagent_structured_raw_json"
HELPER = "mineagent_rd_canonical_json"
PINS = {
    "025": "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3",
    "018": "7aab728d7e5300e8d8718663276b8cba8c7262f38af56230a0d463297f596bc3",
}
KEY = "@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(key),true)"
STRING = "WHEN 'string' THEN result:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(v#>>'{}'),true);"
QUERY = text(
    str(CATALOG).replace(
        "p.prosupport::text",
        "p.prosupport::text, pg_catalog.pg_get_function_arguments(p.oid), p.provariadic=0, p.proargmodes, p.proallargtypes",
    )
)


def sources():
    result = {}
    for revision, expected in PINS.items():
        source = historical(revision, "UPGRADE_SQL")
        if sha256(source.encode()).hexdigest() != expected:
            raise ValueError("Exact raw ASCII predecessor source required")
        result[revision] = source
    return result


def original_sql():
    return _function(sources()["025"], FUNCTION)


def helper_sql():
    return _function(sources()["018"], HELPER)


def plain(expression):
    return (
        f"pg_catalog.octet_length({expression})<=1048574 "
        f"AND {expression} COLLATE \"C\" !~ '[^ -~]' "
        f"AND pg_catalog.strpos({expression},pg_catalog.chr(34))=0 "
        f"AND pg_catalog.strpos({expression},pg_catalog.chr(92))=0"
    )


def candidate_sql():
    old = original_sql()
    if old.count(KEY) != 1 or old.count(STRING) != 1:
        raise ValueError("Exact raw ASCII expression layout required")
    return old.replace(
        KEY,
        f"(CASE WHEN {plain('key')} THEN pg_catalog.chr(34)||key||pg_catalog.chr(34) ELSE {KEY} END)",
    ).replace(
        STRING,
        "WHEN 'string' THEN\n  token:=v#>>'{}';\n"
        f"  IF {plain('token')} THEN result:=pg_catalog.chr(34)||token||pg_catalog.chr(34);\n"
        "  ELSE result:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(token),true); END IF;",
    )


def verify(connection, name, sql):
    rows = connection.execute(QUERY, {"name": name}).all()
    if len(rows) != 1:
        raise ValueError("Exact raw ASCII function identity required")
    row = tuple(rows[0])
    arguments, types, names = (
        ("v json, depth integer DEFAULT 0", "114 23", ["v", "depth"])
        if name == FUNCTION
        else (
            "v jsonb, ascii_only boolean, depth integer DEFAULT 0",
            "3802 16 23",
            ["v", "ascii_only", "depth"],
        )
    )
    if (
        row[0] != _body(sql.replace("@SCHEMA@", '"public"'))
        or row[1:15]
        != (
            "plpgsql",
            "i",
            "u",
            True,
            False,
            False,
            False,
            "f",
            100,
            0,
            ["search_path=pg_catalog"],
            "text",
            types,
            names,
        )
        or row[16] is not None
        or row[18:] != ("-", arguments, True, None, None)
    ):
        raise ValueError("Raw ASCII body or catalog drift")
    return row


def prepare(engine, fixture):
    selected = os.environ.get(ENV)
    if selected is None:
        return None
    if selected not in ("control", "candidate"):
        raise ValueError("Explicit raw ASCII experiment mode required")
    old, new, helper = original_sql(), candidate_sql(), helper_sql()
    with engine.begin() as connection:
        _fixture(connection, engine, fixture)
        helper_before = verify(connection, HELPER, helper)
        before = verify(connection, FUNCTION, old)
        if selected == "candidate":
            connection.exec_driver_sql(
                new.replace("@SCHEMA@", '"public"'),
                execution_options={"no_parameters": True},
            )
        after = verify(connection, FUNCTION, new if selected == "candidate" else old)
        helper_after = verify(connection, HELPER, helper)
        if after[1:] != before[1:] or helper_after != helper_before:
            raise ValueError("Raw ASCII replacement changed catalog or helper")
    return {
        "mode": selected,
        "revision": "032c_oe_provider_table_checks",
        "scope": "disposable_fixture_only",
        "production_acceptance": False,
        "schema_state": "full032c_experimental_raw_ascii"
        if selected == "candidate"
        else "full032c_unmodified",
        "raw_original_body_sha256": sha256(before[0].encode()).hexdigest(),
        "raw_installed_body_sha256": sha256(after[0].encode()).hexdigest(),
        "canonical_helper_body_sha256": sha256(helper_before[0].encode()).hexdigest(),
        "source_sha256": PINS,
        "plain_input_max_bytes": 1048574,
        "canonical_output_max_bytes": 1048576,
    }
