"""Isolated032g experiment, NEVER a migration or runtime optimization.

Only execution_attempts UPDATE events whose entire row is unchanged except the
2 lease timestamps bypass this single reverse validator. All other trigger
functions/events remain unchanged. This deliberately changes owner contention
and owner-wide validation outcomes; it is not original-outcome equivalence.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path

from backend.app.row_validation_oe_032g_base_sql import literal
from script import heartbeat_graph_inventory as graph
from script import heartbeat_schema_inventory as schema_inventory

NAME = "mineagent_oe_workflow_reverse"
PINS_SHA256 = "39f2fb89afe66596e8420632e396f62a90bf26deb078652d7248e324db96dd6d"
# A normalization surrogate only, never an installed schema or DB connection.
SURROGATE = "tradegrad_test_" + "0" * 32
GUARD = """ IF TG_TABLE_SCHEMA='@SCHEMA@' AND TG_TABLE_NAME='execution_attempts' AND TG_OP='UPDATE' THEN
  IF (to_jsonb(OLD)-ARRAY['heartbeat_at','lease_expires_at']) IS NOT DISTINCT FROM
     (to_jsonb(NEW)-ARRAY['heartbeat_at','lease_expires_at']) THEN
   RETURN NULL;
  END IF;
 END IF;
"""


def pins():
    raw = Path(__file__).with_name("reverse_heartbeat_candidate_pins.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINS_SHA256:
        raise ValueError("Exact frozen installed pin manifest required")
    return json.loads(raw)


def original():
    sql = literal("031_workflow_structured_stage.py")
    found = re.findall(
        r"CREATE OR REPLACE FUNCTION @SCHEMA@\." + NAME + r"\(\).*?\$\$;",
        sql,
        re.DOTALL,
    )
    if len(found) != 1:
        raise ValueError("Exact original reverse function required")
    return found[0]


def candidate():
    source = original()
    if source.count("\nBEGIN\n") != 1:
        raise ValueError("Exact original entry point required")
    return source.replace("\nBEGIN\n", "\nBEGIN\n" + GUARD, 1)


def render(candidate_enabled):
    # Only disposable drill database's public schema is permitted by preflight.
    source = candidate() if candidate_enabled else original()
    return source.replace("'@SCHEMA@'", "'public'").replace("@SCHEMA@", '"public"')


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalize_sql(value):
    # Whole identifier boundary only: never rewrite publication, republic,
    # public_key, or other identifiers containing this schema-name substring.
    return re.sub(r"(?<![A-Za-z0-9_])public(?![A-Za-z0-9_])", SURROGATE, value)


def _normalize_rows(rows):
    textual_sql = {
        "definition",
        "body",
        "configuration",
        "identity_arguments",
        "predicate",
    }
    result = []
    for row in rows:
        normalized = dict(row)
        for key, value in normalized.items():
            if key in {"function_schema", "referenced_schema"} and value == "public":
                normalized[key] = SURROGATE
            elif key in textual_sql and isinstance(value, str):
                normalized[key] = _normalize_sql(value)
        result.append(normalized)
    return result


def _original_body():
    return _normalize_sql(render(False).split("$$")[1]).replace(SURROGATE, "@SCHEMA@")


def _candidate_body():
    return _normalize_sql(render(True).split("$$")[1]).replace(SURROGATE, "@SCHEMA@")


def restored_definition_hash(body, definition):
    # Normalize ONLY the exact candidate body back to its frozen original. All
    # header attributes (COST, ROWS, parallel, security, settings, etc.) survive.
    body = body.replace(SURROGATE, "@SCHEMA@")
    definition = definition.replace(SURROGATE, "@SCHEMA@")
    expected = _candidate_body()
    if body != expected or definition.count(expected) != 1:
        raise ValueError("Exact candidate definition body required")
    restored = definition.replace(expected, _original_body(), 1)
    return hashlib.sha256(restored.encode()).hexdigest()


def inventory(connection):
    if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise ValueError("Read-only candidate preflight required")
    database, schema = connection.exec_driver_sql(
        "SELECT current_database(),current_schema()"
    ).one()
    if schema != "public" or not re.fullmatch(
        r"tradegrad_backup_test_[0-9a-f]{32}", database
    ):
        raise ValueError("Owned disposable public-schema database required")
    if connection.exec_driver_sql("SHOW server_version_num").scalar_one() != "180006":
        raise ValueError("Exact PostgreSQL18.6 required")
    if connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalars().all() != [graph.REVISION]:
        raise ValueError("Exact current032g required")
    original_rows = [
        _normalize_rows(connection.exec_driver_sql(q).mappings())
        for q in (graph.TRIGGERS_QUERY, graph.FUNCTIONS_QUERY)
    ]
    result = graph.summarize(SURROGATE, *original_rows)
    targets = [row for row in original_rows[1] if row["name"] == NAME]
    if len(targets) != 1:
        raise ValueError("Exact reverse function required")
    if targets[0]["body"].replace(SURROGATE, "@SCHEMA@") == _candidate_body():
        result["restored_reverse_definition_sha256"] = restored_definition_hash(
            targets[0]["body"], targets[0]["definition"]
        )
    rows = [
        _normalize_rows(connection.exec_driver_sql(q).mappings())
        for q in (
            schema_inventory.TRIGGERS_QUERY,
            schema_inventory.CONSTRAINTS_QUERY,
            schema_inventory.COLUMNS_QUERY,
        )
    ]
    result.update(schema_inventory.summarize(SURROGATE, *rows))
    return result


def verify(result, *, candidate_enabled=False):
    manifest = pins()
    function_names, relation_names = (
        set(manifest["function_names"]),
        set(manifest["relation_names"]),
    )
    selected = {
        "functions": sorted(
            [dict(row) for row in result["functions"] if row["name"] in function_names],
            key=lambda row: (row["name"], row["identity_arguments"]),
        )
    }
    for key in ("schema_triggers", "schema_constraints", "schema_columns"):
        selected[key] = sorted(
            [row for row in result[key] if row["relation"] in relation_names],
            key=lambda row: (
                row["relation"],
                row.get("name") or "",
                row.get("ordinal", 0),
            ),
        )
    if candidate_enabled:
        targets = [row for row in selected["functions"] if row["name"] == NAME]
        if len(targets) != 1:
            raise ValueError("Exact candidate function identity required")
        expected_body = _candidate_body()
        if targets[0]["lexical_heartbeat_present"] is not True:
            raise ValueError("Candidate timestamp observation required")
        targets[0]["lexical_heartbeat_present"] = manifest["original_function"][
            "lexical_heartbeat_present"
        ]
        if (
            targets[0]["body_sha256"]
            != hashlib.sha256(expected_body.encode()).hexdigest()
        ):
            raise ValueError("Exact candidate body required")
        if (
            result.get("restored_reverse_definition_sha256")
            != manifest["original_function"]["definition_sha256"]
        ):
            raise ValueError("Candidate full definition attributes differ")
        targets[0]["body_sha256"] = manifest["original_function"]["body_sha256"]
        targets[0]["definition_sha256"] = manifest["original_function"][
            "definition_sha256"
        ]
    for key, rows in selected.items():
        if (
            len(rows) != manifest["counts"][key]
            or _digest(rows) != manifest["digests"][key]
        ):
            raise ValueError("Frozen installed coverage differs: " + key)


def check(engine, candidate_enabled=False):
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        result = inventory(connection)
        verify(result, candidate_enabled=candidate_enabled)
    return result


@contextmanager
def installed_candidate(engine):
    check(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(render(True))
    try:
        check(engine, True)
        yield
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(render(False))
        check(engine)
