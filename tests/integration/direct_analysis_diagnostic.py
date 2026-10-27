"""Test-only finite predicate observations after real publisher flush, before COMMIT.

Never logs payloads, IDs, SQL parameters or credentials. Does not replace installed
functions, change constraints, settle resources or grant execution authority.
"""

import re
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

LABELS = (
    "stopped_result",
    "outcome_scope",
    "admission_binding",
    "input_binding",
    "receipt_hashes",
    "result_contract",
    "measurement_contract",
    "result_raw",
    "result_size",
    "extraction_hash",
    "extraction_size",
    "protocol_hash",
    "measurement_raw",
    "full_charge",
)


def predicate_clauses():
    from backend.app.factor_analysis_sql import ANALYSIS_SQL

    start = ANALYSIS_SQL.index(" IF NOT @SCHEMA@.mineagent_analysis_stopped(")
    end = ANALYSIS_SQL.index(
        " THEN RAISE EXCEPTION 'Direct analysis outcome lacks", start
    )
    value = ANALYSIS_SQL[start + 4 : end]
    clauses, offset, depth, quoted = [], 0, 0, False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if quoted and value[index : index + 2] == "''":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and value[index : index + 4] == " OR ":
                clauses.append(value[offset:index].strip())
                offset = index + 4
        index += 1
    clauses.append(value[offset:].strip())
    assert len(clauses) == len(LABELS) + 1 and depth == 0 and not quoted
    assert clauses[-2] == "b.state<>'settled'"
    assert clauses[-1].startswith(
        "NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements"
    )
    clauses[-2:] = [" OR ".join(clauses[-2:])]
    return tuple(clauses)


def observe_direct_outcome(session, analysis_id):
    schema = session.scalar(text("SELECT current_schema()"))
    assert type(schema) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema)
    qualified = '"' + schema + '"'
    expressions = [
        "(" + clause.replace("@SCHEMA@", qualified) + ") IS TRUE AS " + label
        for label, clause in zip(LABELS, predicate_clauses(), strict=True)
    ]
    query = (
        "SELECT "
        + ",".join(expressions)
        + """
FROM @SCHEMA@.factor_direct_analysis_intents a
JOIN @SCHEMA@.factor_analysis_plans p ON p.id=a.plan_id
JOIN @SCHEMA@.factor_direct_analysis_outcomes o ON o.analysis_id=a.id
JOIN @SCHEMA@.research_process_admissions admission ON admission.id=o.admission_id
JOIN @SCHEMA@.research_process_receipts li ON li.admission_id=o.admission_id AND li.kind='launch_intent'
JOIN @SCHEMA@.research_process_receipts ident ON ident.id=o.launch_identity_receipt_id
JOIN @SCHEMA@.research_process_receipts st ON st.id=o.stop_receipt_id
JOIN @SCHEMA@.research_process_receipts ex ON ex.id=o.extraction_receipt_id
JOIN @SCHEMA@.research_budget_reservations b ON b.id=admission.reservation_id
WHERE a.id=CAST(:analysis_id AS uuid) LIMIT 2
""".replace("@SCHEMA@", qualified)
    )
    rows = session.execute(text(query), {"analysis_id": analysis_id}).mappings().all()
    if len(rows) != 1:
        print(
            "DIRECT_ANALYSIS_PREDICATES:diagnostic_unavailable_row_inventory",
            flush=True,
        )
        return
    failed = tuple(label for label in LABELS if rows[0][label] is True)
    print(
        "DIRECT_ANALYSIS_PREDICATES:" + (",".join(failed) if failed else "all_pass"),
        flush=True,
    )
    if "stopped_result" in failed:
        observe_stopped_detail(session, analysis_id, schema)


@contextmanager
def diagnose_direct_outcomes():
    from backend.app import factor_analysis_results

    original = factor_analysis_results.persist_direct_analysis_outcome

    def checked(session, fence, analysis_id, admission_id, settings):
        result = original(session, fence, analysis_id, admission_id, settings)
        # The real publisher has flushed both outcome and full settlement here;
        # its owning transaction has not committed or released the current fence.
        try:
            # Read errors roll back only this diagnostic savepoint, never poison
            # the real publisher transaction or replace its eventual guard error.
            # Connection-level savepoint avoids Session.before_commit: neutral
            # publication scopes correctly veto helper-owned Session commits.
            connection = session.connection()
            with connection.begin_nested():
                observe_direct_outcome(connection, analysis_id)
        except (SQLAlchemyError, ValueError, AssertionError, KeyError, TypeError):
            print("DIRECT_ANALYSIS_PREDICATES:diagnostic_unavailable", flush=True)
        return result

    with patch.object(
        factor_analysis_results, "persist_direct_analysis_outcome", checked
    ):
        yield


STOP_LABELS = (
    "admission",
    "reservation",
    "state",
    "receipt_scope_grammar_hash",
    "duplicate_intent",
    "duplicate_identity",
    "duplicate_stop",
    "duplicate_extraction",
    "receipt_kind",
    "receipt_inventory",
    "intent_binding_or_time_text",
    "deadline_interval",
    "identity_chain",
    "observed_window",
    "extraction_binding",
)
STOP_EXCEPTIONS = ("exception_data", "exception_schema", "exception_other")


def stopped_diagnostic_body(schema, installed):
    from backend.app.analysis_process_ddl import SQL

    assert type(schema) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema)
    source = SQL.split("CREATE FUNCTION @S@.mineagent_analysis_stopped(", 1)[1]
    # Alembic quotes the validated schema identifier when installing literals.
    expected = (
        source.split("AS $$", 1)[1]
        .split("$$;", 1)[0]
        .replace("@S@", '"' + schema + '"')
    )
    # Exact installed bytes, including every original condition; never a loose
    # version/name match. Unknown installed predecessor is diagnostic unavailable.
    assert installed == expected
    tail = "EXCEPTION WHEN OTHERS THEN RETURN false;"
    assert installed.count(tail) == 1
    body = installed.replace(
        tail,
        """EXCEPTION WHEN OTHERS THEN
 RETURN CASE WHEN left(SQLSTATE,2)='22' THEN 'exception_data'
 WHEN left(SQLSTATE,2) IN ('42','55') THEN 'exception_schema'
 ELSE 'exception_other' END;""",
    )
    assert body.count("RETURN false;") == len(STOP_LABELS)
    for label in STOP_LABELS:
        body = body.replace("RETURN false;", f"RETURN '{label}';", 1)
    assert body.count("RETURN true;") == 1
    return body.replace("RETURN true;", "RETURN 'all_pass';")


def observe_stopped_detail(connection, analysis_id, schema):
    # The nested savepoint is ALWAYS rolled back, even on successful diagnosis:
    # no temp function survives and no outer publication commit is introduced.
    nested = connection.begin_nested()
    try:
        installed = connection.scalar(
            text("""
SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname=:schema AND p.proname='mineagent_analysis_stopped'
AND p.proargtypes='2950 16'::oidvector
"""),
            {"schema": schema},
        )
        body = stopped_diagnostic_body(schema, installed)
        connection.exec_driver_sql(
            "CREATE FUNCTION pg_temp.mineagent_test_analysis_stopped(aid uuid, require_result boolean) "
            'RETURNS text LANGUAGE plpgsql STABLE SET search_path="'
            + schema
            + '",pg_catalog AS $diagnostic$'
            + body
            + "$diagnostic$;",
            execution_options={"no_parameters": True},
        )
        label = connection.scalar(
            text("""
SELECT pg_temp.mineagent_test_analysis_stopped(o.admission_id,true)
FROM factor_direct_analysis_outcomes o WHERE o.analysis_id=CAST(:analysis_id AS uuid)
"""),
            {"analysis_id": analysis_id},
        )
        assert label in (*STOP_LABELS, *STOP_EXCEPTIONS, "all_pass")
    finally:
        nested.rollback()
    print("DIRECT_ANALYSIS_STOPPED:" + label, flush=True)
