"""Bounded schema metadata supplement, not dependency or concurrency proof."""

from script.heartbeat_graph_inventory import arguments, digest, identifier

MAX_ROWS = 8192

VERSION = "mineagent.heartbeat-installed-catalog.v2"
TRIGGERS_QUERY = """SELECT c.relname AS relation,t.tgname AS name,
 t.tgtype AS event_bits,t.tgenabled AS enabled,t.tgdeferrable AS deferrable,
 t.tginitdeferred AS initially_deferred,t.tgattr::text AS update_columns,
 fn.nspname AS function_schema,p.proname AS function_name,
 pg_get_function_identity_arguments(p.oid) AS identity_arguments,
 pg_get_triggerdef(t.oid) AS definition,
 CASE WHEN t.tgqual IS NOT NULL THEN pg_get_triggerdef(t.oid) END AS predicate,encode(t.tgargs,'hex') AS arguments,
 t.tgconstraint<>0 AS constraint_trigger
 FROM pg_catalog.pg_trigger t
 JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
 JOIN pg_catalog.pg_namespace fn ON fn.oid=p.pronamespace
 WHERE n.nspname=current_schema() AND NOT t.tgisinternal
 ORDER BY c.relname,t.tgname LIMIT 8193"""
CONSTRAINTS_QUERY = """SELECT c.relname AS relation,k.conname AS name,
 k.contype AS kind,k.condeferrable AS deferrable,
 k.condeferred AS initially_deferred,k.convalidated AS validated,
 k.conkey::text AS columns,k.confkey::text AS referenced_columns,
 rc.relname AS referenced_relation,rn.nspname AS referenced_schema,
 k.confupdtype AS update_action,k.confdeltype AS delete_action,
 k.confmatchtype AS match_type,pg_get_constraintdef(k.oid) AS definition
 FROM pg_catalog.pg_constraint k
 JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_catalog.pg_class rc ON rc.oid=k.confrelid
 LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rc.relnamespace
 WHERE n.nspname=current_schema()
 ORDER BY c.relname,k.conname LIMIT 8193"""
COLUMNS_QUERY = """SELECT c.relname AS relation,a.attnum AS ordinal,
 a.attname AS name,a.attnotnull AS not_null,a.attisdropped AS dropped
 FROM pg_catalog.pg_attribute a
 JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname=current_schema() AND c.relkind IN ('r','p') AND a.attnum>0
 ORDER BY c.relname,a.attnum LIMIT 8193"""


def key_columns(value):
    import re

    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[0-9 {},]*", value)
    ):
        raise ValueError("Unexpected metadata column numbers")
    return value


def summarize(schema, triggers, constraints, columns):
    import re

    if not re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema):
        raise ValueError("Generated disposable schema required")
    if any(
        not rows or len(rows) > MAX_ROWS for rows in (triggers, constraints, columns)
    ):
        raise ValueError(
            "Bounded nonempty schema metadata required: "
            f"triggers={len(triggers)} constraints={len(constraints)} "
            f"columns={len(columns)} maximum={MAX_ROWS}"
        )
    result = {"schema_triggers": [], "schema_constraints": [], "schema_columns": []}
    for row in triggers:
        if row["function_schema"] not in {schema, "pg_catalog"}:
            raise ValueError("Unexpected trigger function namespace")
        if row["enabled"] not in {"O", "A", "R", "D"}:
            raise ValueError("Unexpected trigger enablement")
        if type(row["event_bits"]) is not int or not 0 < row["event_bits"] < 128:
            raise ValueError("Unexpected trigger event bits")
        result["schema_triggers"].append(
            {
                "relation": identifier(row["relation"]),
                "name": identifier(row["name"]),
                "event_bits": row["event_bits"],
                "enabled": row["enabled"],
                **{
                    k: bool(row[k])
                    for k in ("deferrable", "initially_deferred", "constraint_trigger")
                },
                # pg_get_expr cannot deparse trigger OLD+NEW Vars. Preserve
                # their full definition fingerprint, never claim predicate-only.
                "predicate_encoding": "full_trigger_definition"
                if row["predicate"] is not None
                else None,
                "update_columns": key_columns(row["update_columns"]),
                "function_schema": "@SCHEMA@"
                if row["function_schema"] == schema
                else "pg_catalog",
                "function_name": identifier(row["function_name"]),
                "identity_arguments": arguments(row["identity_arguments"], schema),
                **{
                    k + "_sha256": digest(row[k], schema)
                    for k in ("definition", "predicate", "arguments")
                },
            }
        )
    for row in constraints:
        if row["kind"] not in {"c", "f", "p", "u", "t", "x", "n"}:
            raise ValueError("Unexpected constraint kind")
        if row["referenced_schema"] not in {None, schema}:
            raise ValueError("Unexpected constraint namespace")
        if any(
            row[k] not in {" ", "a", "r", "c", "n", "d"}
            for k in ("update_action", "delete_action")
        ) or row["match_type"] not in {" ", "f", "p", "s"}:
            raise ValueError("Unexpected constraint actions")
        result["schema_constraints"].append(
            {
                "relation": identifier(row["relation"]),
                "name": identifier(row["name"]),
                "kind": row["kind"],
                **{
                    k: bool(row[k])
                    for k in ("deferrable", "initially_deferred", "validated")
                },
                "columns": key_columns(row["columns"]),
                "referenced_columns": key_columns(row["referenced_columns"]),
                "referenced_relation": identifier(row["referenced_relation"])
                if row["referenced_relation"] is not None
                else None,
                "referenced_schema": "@SCHEMA@" if row["referenced_schema"] else None,
                **{k: row[k] for k in ("update_action", "delete_action", "match_type")},
                "definition_sha256": digest(row["definition"], schema),
            }
        )
    for row in columns:
        if type(row["ordinal"]) is not int or not 0 < row["ordinal"] <= 1600:
            raise ValueError("Unexpected column ordinal")
        result["schema_columns"].append(
            {
                "relation": identifier(row["relation"]),
                "ordinal": row["ordinal"],
                "name": None if row["dropped"] else identifier(row["name"]),
                "not_null": bool(row["not_null"]),
                "dropped": bool(row["dropped"]),
            }
        )
    return result


def capture(connection, schema):
    # Independently enforce read-only before even metadata retrieval.
    if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise ValueError("Read-only transaction required")
    rows = [
        [dict(row) for row in connection.exec_driver_sql(query).mappings()]
        for query in (TRIGGERS_QUERY, CONSTRAINTS_QUERY, COLUMNS_QUERY)
    ]
    return summarize(schema, *rows)
