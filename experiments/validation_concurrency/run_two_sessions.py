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

from proof_assertions import (
    ProofFailure, expect_commit_error, expect_statement_error, safe_state,
)


EXPECTED_CASES = (
    "initial_uncommitted_visibility_and_gate",
    "writer_progress_after_commit",
    "all_dml_routes_and_noop_statement_gate",
    "missing_receipt_at_commit",
    "missing_parent_at_commit",
    "orphan_receipt_at_commit",
    "immediate_boundary_rejection_then_valid_repair",
    "later_write_after_immediate_then_deferred",
    "valid_immediate_deferred_delete_and_repair",
    "post_savepoint_rows_and_gate_rollback",
    "pre_savepoint_gate_and_pending_invalid_work_retained",
    "gate_release_after_full_rollback",
    "failed_commit_releases_gate",
    "read_committed_statement_sees_later_commit",
    "shared_parent_delete_rejected",
    "caller_flag_has_no_authority",
    "uncontended_truncate_rejected",
    "coordinated_delete_then_recreate",
)


def require_case_completion(records):
    """Reject omitted, duplicated, unknown or reordered case acknowledgments.

    This is a completeness guard, not independent evidence that SQL ran.
    Each case must still make its own phase and state observations.
    """
    if (type(records) is not list
            or any(type(record) is not dict for record in records)
            or tuple(record.get("case") for record in records) != EXPECTED_CASES):
        raise ProofFailure("case manifest incomplete or unexpected", "verify_case_completion",
                           "observation")


def render(schema, key):
    if not re.fullmatch(r"rd_coord_model_[a-f0-9]{32}", schema):
        raise ValueError("invalid owned model schema")
    if type(key) is not int or not 0 < key < 2**63:
        raise ValueError("invalid advisory key")
    return (Path(__file__).with_name("model.sql").read_text()
            .replace("__SCHEMA__", schema).replace("__KEY__", str(key)))


def expected_error(connection, statement, code):
    """Compatibility adapter: now explicitly a statement-time assertion."""
    markers = {"55P03": "model_writer_busy", "23514": "model_graph_invalid",
               "0A000": "model_truncate_unsupported"}
    return expect_statement_error(connection, statement, code, markers[code], stage="adapter")


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


def graph_state(connection, schema):
    if not re.fullmatch(r"rd_coord_model_[a-f0-9]{32}", schema):
        raise ValueError("invalid owned model schema")
    # Internal synthetic values only. Reports contain equality outcomes, not rows.
    return tuple(tuple(connection.execute(query).fetchall()) for query in (
        f"SELECT id FROM {schema}.parent ORDER BY id",
        f"SELECT id,owner_id,parent_id FROM {schema}.workflow ORDER BY id",
        f"SELECT workflow_id FROM {schema}.receipt ORDER BY workflow_id",
    ))


def require_state(connection, schema, expected, stage):
    if graph_state(connection, schema) != expected:
        raise ProofFailure("unexpected graph state", stage, "observation")


def execute_commit(connection, statements):
    with connection.transaction():
        for statement in statements:
            connection.execute(statement)


def exercise_cases(a, b, schema, report):
    """Deterministic overlapping-session cases. No production equivalence claim."""
    def begin(name):
        report["stage"] = name
        return name

    def passed(name, **evidence):
        report["cases_passed"].append({"case": name, **evidence})

    def rejected_commit(name, statements):
        stage = begin(name)
        before = graph_state(b, schema)
        observed = expect_commit_error(a, statements, "23514", "model_graph_invalid", stage=stage)
        require_state(b, schema, before, stage)
        passed(stage, **observed, rollback_state_verified=True)

    stage = begin("initial_uncommitted_visibility_and_gate")
    with a.transaction():
        a.execute(f"INSERT INTO {schema}.parent VALUES (1)")
        a.execute(f"INSERT INTO {schema}.workflow VALUES (1,1,1)")
        a.execute(f"INSERT INTO {schema}.receipt VALUES (1)")
        expect_statement_error(b, f"INSERT INTO {schema}.parent VALUES (2)",
                               "55P03", "model_writer_busy", stage=stage)
        require_state(b, schema, ((), (), ()), stage)
    require_state(b, schema, (((1,),), ((1, 1, 1),), ((1,),)), stage)
    passed(stage, rejection_phase="statement", synthetic_marker_matched=True,
           uncommitted_hidden=True, committed_visible=True)

    stage = begin("writer_progress_after_commit")
    execute_commit(b, [f"INSERT INTO {schema}.parent VALUES (2)"])
    require_state(b, schema, (((1,), (2,)), ((1, 1, 1),), ((1,),)), stage)
    passed(stage, committed_state_verified=True)

    stage = begin("all_dml_routes_and_noop_statement_gate")
    before = graph_state(b, schema)
    statements = {
        "parent_insert": f"INSERT INTO {schema}.parent VALUES (99)",
        "parent_update": f"UPDATE {schema}.parent SET id=id WHERE id=2",
        "parent_delete_noop": f"DELETE FROM {schema}.parent WHERE id=99",
        "workflow_insert": f"INSERT INTO {schema}.workflow VALUES (99,1,1)",
        "workflow_update": f"UPDATE {schema}.workflow SET owner_id=1 WHERE id=1",
        "workflow_delete_noop": f"DELETE FROM {schema}.workflow WHERE id=99",
        "receipt_insert": f"INSERT INTO {schema}.receipt VALUES (99)",
        "receipt_update": f"UPDATE {schema}.receipt SET workflow_id=workflow_id WHERE workflow_id=1",
        "receipt_delete_noop": f"DELETE FROM {schema}.receipt WHERE workflow_id=99",
        "workflow_update_noop": f"UPDATE {schema}.workflow SET owner_id=1 WHERE id=-1",
    }
    with a.transaction():
        a.execute(f"UPDATE {schema}.parent SET id=id WHERE id=1")
        for route, statement in statements.items():
            expect_statement_error(b, statement, "55P03", "model_writer_busy", stage=route)
            require_state(b, schema, before, route)
    passed(stage, checked_routes=list(statements), rejection_phase="statement",
           synthetic_marker_matched=True, unchanged_state_after_each_rejection=True)

    rejected_commit("missing_receipt_at_commit", [f"INSERT INTO {schema}.workflow VALUES (9,1,1)"])
    rejected_commit("missing_parent_at_commit", [f"INSERT INTO {schema}.workflow VALUES (9,1,99)",
                                                 f"INSERT INTO {schema}.receipt VALUES (9)"])
    rejected_commit("orphan_receipt_at_commit", [f"INSERT INTO {schema}.receipt VALUES (9)"])

    stage = begin("immediate_boundary_rejection_then_valid_repair")
    before = graph_state(b, schema)
    with a.transaction():
        a.execute(f"INSERT INTO {schema}.workflow VALUES (2,2,1)")
        observed = expect_statement_error(a, "SET CONSTRAINTS ALL IMMEDIATE",
                                          "23514", "model_graph_invalid", stage=stage)
        a.execute(f"INSERT INTO {schema}.receipt VALUES (2)")
    expected = (before[0], before[1] + ((2, 2, 1),), before[2] + ((2,),))
    require_state(b, schema, expected, stage)
    passed(stage, **observed, insert_succeeded_before_boundary=True, repair_committed=True)

    rejected_commit("later_write_after_immediate_then_deferred", [
        f"UPDATE {schema}.workflow SET owner_id=owner_id WHERE id=2",
        "SET CONSTRAINTS ALL IMMEDIATE", "SET CONSTRAINTS ALL DEFERRED",
        f"DELETE FROM {schema}.receipt WHERE workflow_id=2",
    ])

    stage = begin("valid_immediate_deferred_delete_and_repair")
    before = graph_state(b, schema)
    execute_commit(a, [f"UPDATE {schema}.workflow SET owner_id=owner_id WHERE id=2",
                       "SET CONSTRAINTS ALL IMMEDIATE", "SET CONSTRAINTS ALL DEFERRED",
                       f"DELETE FROM {schema}.receipt WHERE workflow_id=2",
                       f"INSERT INTO {schema}.receipt VALUES (2)"])
    require_state(b, schema, before, stage)
    passed(stage, committed_state_verified=True)

    stage = begin("post_savepoint_rows_and_gate_rollback")
    before = graph_state(b, schema)
    with a.transaction():
        a.execute("SAVEPOINT model_savepoint")
        a.execute(f"INSERT INTO {schema}.workflow VALUES (3,3,1)")
        a.execute("ROLLBACK TO SAVEPOINT model_savepoint")
        require_state(a, schema, before, stage)
        execute_commit(b, [f"INSERT INTO {schema}.parent VALUES (5)"])
        a.execute("SET CONSTRAINTS ALL IMMEDIATE")
    passed(stage, rolled_back_rows_verified=True, other_writer_committed_while_a_open=True,
           deferred_event_queue_cancellation_observed=False)

    stage = begin("pre_savepoint_gate_and_pending_invalid_work_retained")
    with a.transaction():
        a.execute(f"DELETE FROM {schema}.receipt WHERE workflow_id=1")
        a.execute("SAVEPOINT model_savepoint")
        a.execute(f"INSERT INTO {schema}.parent VALUES (6)")
        a.execute("ROLLBACK TO SAVEPOINT model_savepoint")
        expect_statement_error(b, f"INSERT INTO {schema}.parent VALUES (6)",
                               "55P03", "model_writer_busy", stage=stage)
        expect_statement_error(a, "SET CONSTRAINTS ALL IMMEDIATE",
                               "23514", "model_graph_invalid", stage=stage)
        a.execute(f"INSERT INTO {schema}.receipt VALUES (1)")
    execute_commit(b, [f"INSERT INTO {schema}.parent VALUES (6)"])
    passed(stage, pre_savepoint_gate_retained=True, earlier_invalid_work_rejected=True,
           progress_after_repair_commit=True)

    stage = begin("gate_release_after_full_rollback")
    before = graph_state(b, schema)
    class DeliberateRollback(Exception):
        pass
    try:
        with a.transaction():
            a.execute(f"INSERT INTO {schema}.parent VALUES (4)")
            expect_statement_error(b, f"INSERT INTO {schema}.parent VALUES (4)",
                                   "55P03", "model_writer_busy", stage=stage)
            raise DeliberateRollback()
    except DeliberateRollback:
        pass
    require_state(b, schema, before, stage)
    execute_commit(b, [f"INSERT INTO {schema}.parent VALUES (4)"])
    passed(stage, rolled_back_state_verified=True, next_writer_committed=True)

    rejected_commit("failed_commit_releases_gate", [f"DELETE FROM {schema}.receipt WHERE workflow_id=1"])
    execute_commit(b, [f"UPDATE {schema}.workflow SET owner_id=owner_id WHERE id=1"])
    report["cases_passed"][-1]["next_writer_committed"] = True

    stage = begin("read_committed_statement_sees_later_commit")
    with b.transaction():
        before = graph_state(b, schema)
        execute_commit(a, [f"INSERT INTO {schema}.parent VALUES (7)",
                           f"INSERT INTO {schema}.workflow VALUES (7,7,7)",
                           f"INSERT INTO {schema}.receipt VALUES (7)"])
        affected = b.execute(f"UPDATE {schema}.workflow SET owner_id=owner_id WHERE id=7").rowcount
        if affected != 1 or len(graph_state(b, schema)[1]) != len(before[1]) + 1:
            raise ProofFailure("later committed graph not visible", stage, "observation")
    passed(stage, later_statement_saw_committed_row=True, transaction_committed=True)

    rejected_commit("shared_parent_delete_rejected", [f"DELETE FROM {schema}.parent WHERE id=1"])
    rejected_commit("caller_flag_has_no_authority", ["SET LOCAL prototype.validated='true'",
                                                     f"DELETE FROM {schema}.receipt WHERE workflow_id=1"])

    stage = begin("uncontended_truncate_rejected")
    before = graph_state(b, schema)
    observed = expect_statement_error(a, f"TRUNCATE {schema}.receipt", "0A000",
                                      "model_truncate_unsupported", stage=stage)
    require_state(b, schema, before, stage)
    passed(stage, **observed, rollback_state_verified=True)

    stage = begin("coordinated_delete_then_recreate")
    before = graph_state(b, schema)
    execute_commit(a, [f"DELETE FROM {schema}.receipt WHERE workflow_id=2",
                       f"DELETE FROM {schema}.workflow WHERE id=2"])
    execute_commit(a, [f"INSERT INTO {schema}.workflow VALUES (2,2,1)",
                       f"INSERT INTO {schema}.receipt VALUES (2)"])
    require_state(b, schema, before, stage)
    passed(stage, both_transactions_committed=True, final_state_verified=True)


def failure_record(error, stage):
    if isinstance(error, ProofFailure):
        return error.record()
    return {"reason": "unexpected runtime failure", "stage": stage,
            "phase": "runtime", "observed_state": safe_state(error)}


def run(dsn):
    report = {"model_only": True, "passed": False, "actual_rd_predicates": False,
              "production_acceptance": False, "sql_attempted": False,
              "sql_execution_confirmed": False,
              "case_manifest_verified": False,
              "independent_backends": False, "cases_passed": [],
              "stage": "connection_parameters", "cleanup": {"status": "not_started"},
              "scope_limits": ["synthetic_global_gate_only", "no_scoped_sql_restart_latch",
                               "no_commit_validation_barrier", "no_deferred_event_queue_observation",
                               "no_production_writer_or_performance_proof"]}
    installed = False
    try:
        import psycopg
        params = local_connection_parameters(dsn)
        schema = "rd_coord_model_" + uuid.uuid4().hex
        key = int.from_bytes(hashlib.sha256(schema.encode()).digest()[:8], "big") >> 1
        report["stage"] = "connect"
        with psycopg.connect(**params, autocommit=True) as a, psycopg.connect(**params, autocommit=True) as b:
            report["stage"] = "configure_sessions"
            for c in (a, b):
                report["sql_attempted"] = True
                c.execute("SET statement_timeout='3s'")
                report["sql_execution_confirmed"] = True
                c.execute("SET lock_timeout='250ms'")
                if c.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                    raise ProofFailure("READ COMMITTED required", report["stage"], "observation")
            pids = [c.execute("SELECT pg_backend_pid()").fetchone()[0] for c in (a, b)]
            if len(set(pids)) != 2:
                raise ProofFailure("independent backends required", report["stage"], "observation")
            report["independent_backends"] = True
            try:
                report["stage"] = "install_model"
                report["cleanup"] = {"status": "creation_unconfirmed", "owned_schema": schema}
                with a.transaction():
                    a.execute(render(schema, key), prepare=False)
                installed = True
                report["cleanup"]["status"] = "pending"
                exercise_cases(a, b, schema, report)
                report["stage"] = "verify_case_completion"
                require_case_completion(report["cases_passed"])
                report["case_manifest_verified"] = True
            except Exception as error:
                report["failure"] = failure_record(error, report["stage"])
            finally:
                if installed:
                    try:
                        with a.transaction():
                            a.execute(f"DROP SCHEMA {schema} CASCADE")
                        if a.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0] is not None:
                            raise ProofFailure("owned schema remains", "cleanup", "observation")
                        report["cleanup"]["status"] = "verified_absent"
                        installed = False
                    except Exception as error:
                        report["cleanup"]["status"] = "needs_verification"
                        report["cleanup_failure"] = failure_record(error, "cleanup")
            report["stage"] = "connection_close"
    except Exception as error:
        report.setdefault("failure", failure_record(error, report["stage"]))
    report["passed"] = ("failure" not in report and "cleanup_failure" not in report
                        and report["case_manifest_verified"]
                        and report["cleanup"]["status"] == "verified_absent")
    report["cleanup_completed"] = report["cleanup"]["status"] == "verified_absent"
    report["cleanup_requires_verification"] = report["cleanup"]["status"] not in {"not_started", "verified_absent"}
    report["stage"] = "completed" if report["passed"] else "failed"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-permitted-local", action="store_true")
    args = parser.parse_args()
    if not args.execute_permitted_local:
        parser.error("execution requires a separately permitted local environment")
    dsn = os.environ.get("RD_COORD_MODEL_DSN")
    if not dsn:
        parser.error("RD_COORD_MODEL_DSN is required")
    result = run(dsn)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
