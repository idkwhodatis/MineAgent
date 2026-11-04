"""Reconstructed, UNEXECUTED opt-in PostgreSQL global-gate reference runner.

Never run against production. A separately permitted environment is required.
No DSN, SQL text, error payload or row identifiers are printed. Technical opt-in
is not permission to retry a denied socket route. No server is started here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def render(schema, key):
    if not re.fullmatch(r"rd_coord_model_[a-f0-9]{32}", schema):
        raise ValueError("invalid owned model schema")
    if type(key) is not int or not 0 < key < 2**63:
        raise ValueError("invalid advisory key")
    return (Path(__file__).with_name("model.sql").read_text()
            .replace("__SCHEMA__", schema).replace("__KEY__", str(key)))


def expected_error(connection, statement, code):
    import psycopg
    try:
        with connection.transaction():
            connection.execute(statement)
    except psycopg.Error as exc:
        if exc.sqlstate != code:
            raise AssertionError("unexpected database category") from None
    else:
        raise AssertionError("expected rejection missing")


def local_connection_parameters(dsn):
    """Fail closed on libpq ambient routing or implicit account/database choice."""
    from psycopg.conninfo import conninfo_to_dict
    if any(name.startswith("PG") and value for name, value in os.environ.items()):
        raise ValueError("ambient PostgreSQL settings are unsupported")
    params = dict(conninfo_to_dict(dsn))
    allowed = {"host", "port", "dbname", "user", "password", "connect_timeout", "application_name"}
    if not params.keys() <= allowed:
        raise ValueError("unsupported connection parameter")
    host = params.get("host", "")
    if not host.startswith("/") or "," in host:
        raise ValueError("explicit permitted local socket required")
    if not params.get("user") or not params.get("dbname"):
        raise ValueError("explicit database and user required")
    port = params.get("port", "")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("explicit single port required")
    timeout = params.get("connect_timeout", "3")
    if not timeout.isdecimal() or not 1 <= int(timeout) <= 5:
        raise ValueError("bounded connect timeout required")
    params["connect_timeout"] = timeout
    return params


def run(dsn):
    import psycopg
    params = local_connection_parameters(dsn)
    schema = "rd_coord_model_" + uuid.uuid4().hex
    key = int.from_bytes(hashlib.sha256(schema.encode()).digest()[:8], "big") >> 1
    records = []
    installed = False
    with psycopg.connect(**params, autocommit=True) as a, psycopg.connect(**params, autocommit=True) as b:
        for c in (a, b):
            c.execute("SET statement_timeout='3s'")
            c.execute("SET lock_timeout='250ms'")
            if c.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                raise AssertionError("READ COMMITTED required")
        try:
            with a.transaction():
                a.execute(render(schema, key), prepare=False)
            installed = True
            with a.transaction():
                a.execute(f"INSERT INTO {schema}.parent VALUES (1)")
                a.execute(f"INSERT INTO {schema}.workflow VALUES (1,1,1)")
                a.execute(f"INSERT INTO {schema}.receipt VALUES (1)")
                expected_error(b, f"INSERT INTO {schema}.parent VALUES (2)", "55P03")
                expected_error(b, f"INSERT INTO {schema}.workflow VALUES (2,2,1)", "55P03")
                if b.execute(f"SELECT count(*) FROM {schema}.workflow").fetchone()[0] != 0:
                    raise AssertionError("uncommitted model rows became visible")
            if b.execute(f"SELECT count(*) FROM {schema}.workflow").fetchone()[0] != 1:
                raise AssertionError("committed model row missing")
            records.append("direct_sql_and_phantom_exclusion")
            with b.transaction():
                b.execute(f"INSERT INTO {schema}.parent VALUES (2)")
            records.append("writer_progress_after_commit")
            expected_error(a, f"INSERT INTO {schema}.workflow VALUES (2,2,1)", "23514")
            records.append("deferred_orphan_rejection")
            expected_error(a, f"INSERT INTO {schema}.workflow VALUES (2,2,1); SET CONSTRAINTS ALL IMMEDIATE", "23514")
            records.append("immediate_orphan_rejection")
            with a.transaction():
                a.execute("SAVEPOINT model_savepoint")
                a.execute(f"INSERT INTO {schema}.workflow VALUES (2,2,1)")
                a.execute("ROLLBACK TO SAVEPOINT model_savepoint")
                with b.transaction():
                    b.execute(f"INSERT INTO {schema}.parent VALUES (3)")
                a.execute("SET CONSTRAINTS ALL IMMEDIATE")
            records.append("savepoint_rows_events_and_lock_rollback")
            with a.transaction():
                a.execute(f"INSERT INTO {schema}.workflow VALUES (2,2,1)")
                a.execute(f"INSERT INTO {schema}.receipt VALUES (2)")
                a.execute("SET CONSTRAINTS ALL IMMEDIATE")
                a.execute("SET CONSTRAINTS ALL DEFERRED")
                a.execute(f"UPDATE {schema}.workflow SET owner_id=3 WHERE id=2")
            records.append("valid_deferred_immediate_switch")
            expected_error(a, f"DELETE FROM {schema}.parent WHERE id=1", "23514")
            records.append("shared_parent_rejection")
            expected_error(a, f"TRUNCATE {schema}.receipt", "0A000")
            records.append("truncate_fail_closed")
            expected_error(a, f"SET LOCAL prototype.validated='true'; DELETE FROM {schema}.receipt WHERE workflow_id=1", "23514")
            records.append("caller_flag_has_no_authority")
        finally:
            if installed:
                with a.transaction():
                    a.execute(f"DROP SCHEMA {schema} CASCADE")
                installed = False
    return {"model_only": True, "actual_rd_predicates": False,
            "production_acceptance": False, "cases_passed": records,
            "cleanup_completed": not installed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-permitted-local", action="store_true")
    args = parser.parse_args()
    if not args.execute_permitted_local:
        parser.error("execution requires a separately permitted local environment")
    dsn = os.environ.get("RD_COORD_MODEL_DSN")
    if not dsn:
        parser.error("RD_COORD_MODEL_DSN is required")
    try:
        result = run(dsn)
    except Exception:
        print(json.dumps({"model_only": True, "passed": False,
                          "cleanup_requires_verification": True}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
