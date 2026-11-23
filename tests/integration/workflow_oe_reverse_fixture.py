"""Opt-in 032h-only closed diagnostics; never change accepted evidence."""

from contextlib import contextmanager

from psycopg.errors import RaiseException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from script.workflow_heartbeat_production_report import (
    _OE_ATTACHMENT_PROBE,
    _OE_CLAIM_TRIGGER_LABELS,
)

_REVERSE = "Workflow OE reverse immutable graph differs"
_ISOLATED_REVISION = "032h_workflow_oe_run_guard"


def _raise_label(error):
    if isinstance(error, DBAPIError) and type(error.orig) is RaiseException:
        message = error.orig.diag.message_primary
        if type(message) is str:
            return _OE_CLAIM_TRIGGER_LABELS.get(message)
    return None


# SQLSTATE only, except the fixed English PostgreSQL timeout primary label to
# avoid claiming that every 57014 originated in our statement_timeout budget.
_ATTACHMENT_SQLSTATES = {
    "P0002": "probe_strict_missing_row",
    "P0003": "probe_strict_multiple_rows",
    "42601": "probe_sql_definition",
    "42702": "probe_sql_definition",
    "42703": "probe_sql_definition",
    "42804": "probe_sql_definition",
    "42883": "probe_sql_definition",
    "42P01": "probe_schema",
    "3F000": "probe_schema",
}


def _attachment_label(outcome):
    if type(outcome) is list:
        if len(outcome) == 1:
            return "accepted"
        return "probe_result_multiple" if outcome else "probe_result_missing"
    known = _raise_label(outcome)
    if known in _OE_ATTACHMENT_PROBE:
        return known
    if isinstance(outcome, DBAPIError):
        sqlstate = getattr(outcome.orig, "sqlstate", None)
        if type(sqlstate) is not str:
            return "unknown"
        if sqlstate == "57014":
            diagnostic = getattr(outcome.orig, "diag", None)
            primary = getattr(diagnostic, "message_primary", None)
            return (
                "probe_statement_timeout"
                if primary == "canceling statement due to statement timeout"
                else "probe_query_cancelled"
            )
        return _ATTACHMENT_SQLSTATES.get(sqlstate, "unknown")
    return "unknown"


def _observe_savepoint(session, callback):
    """Always roll back the observer's transaction-local timeout and reads."""
    try:
        nested = session.begin_nested()
        try:
            session.execute(text("SET LOCAL statement_timeout = '750ms'"))
            return callback()
        finally:
            nested.rollback()
    except DBAPIError as exc:
        return exc


def _observe_claim(session, owner_id, command, receipt):
    if not session.is_active or session.new or session.dirty or session.deleted:
        return {}
    params = {
        "owner": owner_id,
        "workflow": command.workflow_id,
        "run": receipt.child_run_id,
        "stage": receipt.stage_id,
        "submission": receipt.submission_id,
    }

    def roots():
        if session.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).all() != [(_ISOLATED_REVISION,)]:
            return None
        if (
            session.execute(
                text(
                    "SELECT count(*) FROM public.oe_workflow_submissions WHERE "
                    "owner_id=:owner AND workflow_id=:workflow AND run_id=:run "
                    "AND stage_id=:stage AND submission_id=:submission"
                ),
                params,
            ).scalar_one()
            != 1
        ):
            return None
        return session.execute(
            text(
                "SELECT candidate.id=:workflow AS active, "
                "public.mineagent_workflow_consistent(:owner,candidate.id) IS TRUE AS valid "
                "FROM (SELECT id FROM public.research_workflows WHERE owner_id=:owner AND "
                """plan_canonical::jsonb->>'schema_version'=
                'mineagent.rd-structured-guidance-workflow-plan.v1'
                ORDER BY id LIMIT 4) AS candidate"""
            ),
            params,
        ).all()

    observed = _observe_savepoint(session, roots)
    if (
        type(observed) is not list
        or not (1 <= len(observed) <= 3)
        or sum(active is True for active, _ in observed) != 1
    ):
        return {}
    active_valid = next(valid for active, valid in observed if active)
    sibling_valid = all(valid for active, valid in observed if not active)
    scope = {
        (True, True): "all_consistent",
        (False, True): "active",
        (True, False): "sibling",
        (False, False): "active_and_sibling",
    }[(active_valid, sibling_valid)]
    result = {"oe_reverse_root": scope}
    if active_valid:
        return result

    def attachment():
        return session.execute(
            text(
                "SELECT public.mineagent_oe_workflow_attachment(id) FROM "
                "public.oe_workflow_submissions WHERE owner_id=:owner AND workflow_id=:workflow "
                "AND run_id=:run AND stage_id=:stage AND submission_id=:submission"
            ),
            params,
        ).all()

    outcome = _observe_savepoint(session, attachment)
    result["oe_reverse_attachment"] = _attachment_label(outcome)
    return result


@contextmanager
def observe_032h_oe_reverse(monkeypatch, record_property, *, enabled):
    if not enabled:
        yield
        return

    from backend.app import research_workflow_oe_claim as claims

    observed = []
    original = claims.claim_oe_stage

    def claim(session, owner_id, command, settings, *, admission):
        receipt = original(session, owner_id, command, settings, admission=admission)
        try:
            observed.append(_observe_claim(session, owner_id, command, receipt))
        except Exception:  # noqa: BLE001 - no observer failure replaces the claim
            # Every started savepoint is rolled back in _observe_savepoint.
            # Never roll back or repair the outer transaction; its normal
            # publication code must retain authority over the result.
            observed.append({})
        return receipt

    with monkeypatch.context() as scoped:
        scoped.setattr(claims, "claim_oe_stage", claim)
        try:
            yield
        except DBAPIError as exc:
            if _raise_label(exc) == "reverse_graph" and len(observed) == 1:
                try:
                    for key, value in observed[0].items():
                        record_property("rd_drain_" + key, value)
                except Exception:  # noqa: BLE001, S110 - retain the original database error
                    pass
            raise
