"""Read-only installed032g metadata evidence; NOT dependency/equivalence proof.

No function bodies, SQL, application rows, connection details or settings leave
capture(). Named references are lexical observations, not resolved dependencies.
"""

import hashlib
import json
import re
from pathlib import Path

REVISION = "032g_schema_keyset_cost"
MAX_ROWS = 4096
MAX_BYTES = 2_000_000
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
TRIGGERS_QUERY = """SELECT t.tgname AS name,t.tgisinternal AS internal,
 t.tgtype AS event_bits,t.tgenabled AS enabled,t.tgdeferrable AS deferrable,
 t.tginitdeferred AS initially_deferred,t.tgattr::text AS update_columns,
 n.nspname AS function_schema,p.proname AS function_name,
 pg_get_function_identity_arguments(p.oid) AS identity_arguments,
 pg_get_triggerdef(t.oid) AS definition,
 pg_get_expr(t.tgqual,t.tgrelid) AS predicate,encode(t.tgargs,'hex') AS arguments,
 t.tgconstraint<>0 AS constraint_trigger
 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE t.tgrelid='execution_attempts'::regclass ORDER BY t.tgname LIMIT 4097"""
FUNCTIONS_QUERY = """SELECT p.proname AS name,
 pg_get_function_identity_arguments(p.oid) AS identity_arguments,
 l.lanname AS language,p.provolatile AS volatility,p.prosecdef AS security_definer,
 p.proisstrict AS strict,p.proparallel AS parallel,p.prokind AS kind,
 p.proleakproof AS leakproof,p.prosrc AS body,
 pg_get_functiondef(p.oid) AS definition,p.proconfig::text AS configuration,
 p.proacl::text AS acl
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_language l ON l.oid=p.prolang
 WHERE n.nspname=current_schema() AND p.prokind='f'
 ORDER BY p.proname,identity_arguments LIMIT 4097"""


def digest(value, schema):
    if value is None:
        return None
    return hashlib.sha256(value.replace(schema, "@SCHEMA@").encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Unexpected metadata identifier")
    return value


def arguments(value, schema):
    value = value.replace(schema, "SCHEMA")
    if len(value) > 2048 or not re.fullmatch(r'[A-Za-z_0-9 ,."\[\]]*', value):
        raise ValueError("Unexpected function signature metadata")
    return value


def summarize(schema, triggers, functions):
    if not re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema):
        raise ValueError("Generated disposable schema required")
    if not triggers or not functions or max(len(triggers), len(functions)) > MAX_ROWS:
        raise ValueError("Bounded nonempty metadata inventory required")
    function_names = {identifier(row["name"]) for row in functions}
    result = {
        "revision": REVISION,
        "server_version_num": 180006,
        "scope": "execution_attempts triggers and current-schema functions",
        "dependency_status": "lexical observations only; static closure NOT established",
        "source_provenance_status": "installed hashes observed; frozen-source manifest NOT verified",
        "gaps": [
            "dynamic SQL, operators, casts, overloads, whole-row escapes and external calls require review"
        ],
        "triggers": [],
        "functions": [],
    }
    for row in triggers:
        if row["function_schema"] not in {schema, "pg_catalog"}:
            raise ValueError("Unexpected trigger function namespace")
        if row["enabled"] not in {"O", "A", "R", "D"}:
            raise ValueError("Unexpected trigger enablement")
        columns = row["update_columns"]
        if not re.fullmatch(r"[0-9 ]*", columns):
            raise ValueError("Unexpected trigger columns")
        result["triggers"].append(
            {
                "name": identifier(row["name"]),
                "internal": bool(row["internal"]),
                "event_bits": int(row["event_bits"]),
                "enabled": row["enabled"],
                "deferrable": bool(row["deferrable"]),
                "initially_deferred": bool(row["initially_deferred"]),
                "constraint_trigger": bool(row["constraint_trigger"]),
                "update_columns": columns,
                "function_schema": "@SCHEMA@"
                if row["function_schema"] == schema
                else "pg_catalog",
                "function_name": identifier(row["function_name"]),
                "identity_arguments": arguments(row["identity_arguments"], schema),
                **{
                    key + "_sha256": digest(row[key], schema)
                    for key in ("definition", "predicate", "arguments")
                },
            }
        )
    for row in functions:
        body = row["body"]
        # Deliberately conservative: strings/comments may produce false positives.
        # Never turn this lexical inventory into a dependency-absence assertion.
        references = sorted(
            set(re.findall(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\(", body)) & function_names
        )
        if len(references) > 512:
            raise ValueError("Function reference bound exceeded")
        result["functions"].append(
            {
                "name": identifier(row["name"]),
                "identity_arguments": arguments(row["identity_arguments"], schema),
                **{
                    key: identifier(row[key])
                    for key in ("language", "volatility", "parallel", "kind")
                },
                **{
                    key: bool(row[key])
                    for key in ("security_definer", "strict", "leakproof")
                },
                **{
                    key + "_sha256": digest(row[key], schema)
                    for key in ("body", "definition", "configuration", "acl")
                },
                "lexical_local_references": references,
                "lexical_execute_present": bool(
                    re.search(r"\bEXECUTE\b", body, re.IGNORECASE)
                ),
                "lexical_heartbeat_present": bool(
                    re.search(
                        r"\b(?:heartbeat_at|lease_expires_at)\b", body, re.IGNORECASE
                    )
                ),
                "lexical_row_lock_present": bool(
                    re.search(
                        r"\bFOR\s+(?:(?:NO\s+KEY\s+)?UPDATE|(?:KEY\s+)?SHARE)\b",
                        body,
                        re.IGNORECASE,
                    )
                ),
                "lexical_whole_row_present": bool(
                    re.search(
                        r"\b(?:to_jsonb|row_to_json)\s*\(|SELECT\s+\*",
                        body,
                        re.IGNORECASE,
                    )
                ),
            }
        )
    return result


def capture(connection):
    if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise ValueError("Read-only transaction required")
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    if not re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema):
        raise ValueError("Generated disposable schema required")
    if connection.exec_driver_sql("SHOW server_version_num").scalar_one() != "180006":
        raise ValueError("Exact PostgreSQL18.6 required")
    if connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalars().all() != [REVISION]:
        raise ValueError("Exact032g required")
    triggers = [
        dict(row) for row in connection.exec_driver_sql(TRIGGERS_QUERY).mappings()
    ]
    functions = [
        dict(row) for row in connection.exec_driver_sql(FUNCTIONS_QUERY).mappings()
    ]
    from script import heartbeat_schema_inventory

    result = summarize(schema, triggers, functions)
    result["schema_version"] = heartbeat_schema_inventory.VERSION
    result["scope"] = (
        "execution_attempts triggers, all current-schema user triggers, table constraints, columns and functions"
    )
    result.update(heartbeat_schema_inventory.capture(connection, schema))
    return result


def export(connection, directory):
    directory = Path(directory)
    if (
        directory.resolve() != (Path.cwd() / "var/sql-candidate-proof").resolve()
        or directory.is_symlink()
    ):
        raise ValueError("Bounded proof output directory required")
    result = capture(connection)
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    if len(data) > MAX_BYTES:
        raise ValueError("Metadata artifact bound exceeded")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "heartbeat-installed-032g.json").open("xb") as stream:
        stream.write(data)
    return result
