"""Test-only closed DB failure categories; never SQL, values or error messages."""

import json
from collections import deque
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import event

STATES = {
    "42601": "syntax_error",
    "42883": "undefined_function",
    "42P01": "undefined_table",
    "42703": "undefined_column",
    "42804": "datatype_mismatch",
    "42P10": "invalid_column_reference",
    "22023": "invalid_parameter",
    "22P02": "invalid_text_representation",
    "23502": "not_null_violation",
    "23505": "unique_violation",
    "23503": "foreign_key_violation",
    "23514": "check_violation",
    "40001": "serialization_failure",
    "40P01": "deadlock",
    "55P03": "lock_unavailable",
    "57014": "query_cancelled",
    "25P02": "transaction_aborted",
    "08003": "connection_failure",
    "08006": "connection_failure",
    "P0001": "raised_exception",
}


# Exact full static RAISE literals only; never render the server message.
RAISED_CATEGORIES = {
    "workflow owner is busy": "workflow_owner_busy",
    "Workflow immutable finite graph differs": "workflow_graph",
    "Workflow OE immutable predecessor/input/submission graph differs": "oe_workflow_attachment",
    "Workflow OE definition differs from actual predecessor": "oe_workflow_definition",
    "Workflow OE manual input bytes differ": "oe_workflow_manual_input",
    "Workflow OE derived input bytes differ": "oe_workflow_derived_input",
    "Workflow OE text hash differs": "oe_workflow_text_hash",
    "Workflow OE exact frozen preflight differs": "oe_workflow_preflight",
    "Workflow OE requires durable original queue and budget": "oe_workflow_queue_budget",
    "Workflow OE root graph differs": "oe_workflow_root",
    "Workflow OE reverse immutable graph differs": "oe_workflow_reverse",
    "Workflow OE reverse bound": "oe_workflow_reverse_bound",
    "Workflow declaration origin/context differs": "oe_workflow_declaration",
    "Workflow declaration attachment missing": "oe_workflow_attachment_missing",
    "Legacy user declaration forbids workflow attachment": "oe_user_attachment_forbidden",
    "Seed origin differs from exact declaration": "oe_seed_origin",
    "Structured authenticated declaration differs": "oe_authenticated_declaration",
}


# Exact table/constraint pairs captured from the migrated032a catalog.
CHECK_CATEGORIES = {
    (
        "structured_strategy_versions",
        "structured_strategy_versions_check",
    ): "structured_version_shape",
    ("research_runs", "ck_research_run_identity"): "run_identity",
    ("research_runs", "ck_research_run_legacy_config"): "run_config",
    ("research_runs", "ck_research_run_status"): "run_status",
    ("research_runs", "research_runs_generation_check"): "run_generation",
    (
        "oe_structured_submissions",
        "oe_structured_submissions_check",
    ): "oe_submission_shape",
    ("oe_structured_specs", "oe_structured_specs_check"): "oe_spec_hashes",
    ("oe_structured_specs", "oe_structured_specs_check1"): "oe_spec_shape",
    ("data_usage_exposures", "data_usage_exposures_check"): "exposure_window",
    ("research_budget_controls", "ck_neutral_budget_vectors"): "budget_vectors",
}


def classify(context):
    try:
        error = context.original_exception
        state = getattr(error, "sqlstate", None)
        if state is None:
            state = getattr(error, "pgcode", None)
        if type(state) is str and state in STATES:
            result = {"sqlstate": state, "family": STATES[state]}
            if state == "P0001":
                primary = getattr(getattr(error, "diag", None), "message_primary", None)
                result["category"] = (
                    RAISED_CATEGORIES.get(primary, "other")
                    if type(primary) is str
                    else "other"
                )
            if state == "23514":
                diag = getattr(error, "diag", None)
                table = getattr(diag, "table_name", None)
                constraint = getattr(diag, "constraint_name", None)
                result["category"] = (
                    CHECK_CATEGORIES.get((table, constraint), "other")
                    if type(table) is str and type(constraint) is str
                    else "other"
                )
            return result
    except Exception:  # noqa: BLE001, S110 -- diagnostic must not replace DB failure
        pass
    return {"sqlstate": "other", "family": "other"}


@contextmanager
def diagnose_submission_sql(engine):
    records, lock = deque(maxlen=8), Lock()
    count = 0

    def record(context):
        nonlocal count
        try:
            closed = classify(context)
            with lock:
                count = min(count + 1, 10000)
                records.append(closed)
        except Exception:  # noqa: BLE001, S110 -- diagnostic only
            pass

    event.listen(engine, "handle_error", record)
    try:
        yield
    finally:
        event.remove(engine, "handle_error", record)
        try:
            with lock:
                report = {"count": count, "tail": list(records)}
            print("SUBMISSION_SQL_FAILURES:" + json.dumps(report, sort_keys=True))
        except Exception:  # noqa: BLE001, S110 -- never replace original assertion
            pass
