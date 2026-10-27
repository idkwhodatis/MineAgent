"""Outcome accounting authority doubles only; no actual process/PG claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.factor_analysis_results import _reservation
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector


def fixture(state="reserved", *, evaluator=1, settlement=None):
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    amounts = BudgetVector(
        process_slots=1,
        evaluator_executions=evaluator,
        wall_milliseconds=60000,
        storage_bytes=67174400,
    ).model_dump(mode="json")
    row = SimpleNamespace(
        **vars(fence), id=uuid4(), purpose="process_unit", state=state, amounts=amounts
    )
    receipt = SimpleNamespace(admission=SimpleNamespace(reservation_id=row.id))
    session = SimpleNamespace(get=lambda *args: row, scalar=lambda *args: settlement)
    return session, fence, receipt, row


def test_held_full_analysis_charge_can_finalize():
    session, fence, receipt, row = fixture()
    assert _reservation(session, fence, receipt, completed=False)[0] is row


def test_analysis_without_evaluator_charge_rejected():
    session, fence, receipt, _ = fixture(evaluator=0)
    with pytest.raises(DomainError):
        _reservation(session, fence, receipt, completed=False)


def test_cached_measurement_requires_settlement():
    session, fence, receipt, _ = fixture()
    with pytest.raises(DomainError):
        _reservation(session, fence, receipt, completed=True)


@pytest.mark.parametrize("outcome", ["actual", "released", "conservative_unknown"])
def test_refunded_or_wrong_outcome_rejected(outcome):
    session, fence, receipt, row = fixture("settled")
    charge = dict(row.amounts)
    if outcome == "conservative_unknown":
        charge["evaluator_executions"] = 0
    session.scalar = lambda *args: SimpleNamespace(charge=charge, outcome=outcome)
    with pytest.raises(DomainError):
        _reservation(session, fence, receipt, completed=True)


def test_exact_full_settlement_allows_cached_reread(monkeypatch, capsys):
    session, fence, receipt, row = fixture("settled")
    session.scalar = lambda *args: SimpleNamespace(
        charge=row.amounts, outcome="conservative_unknown"
    )
    assert _reservation(session, fence, receipt, completed=True)[0] is row

    # Test-only runtime diagnostic observes completed publisher flushes without
    # changing their return value, commit ownership or original exceptions.
    from contextlib import contextmanager

    from backend.app import factor_analysis_results
    from tests.integration.direct_analysis_diagnostic import (
        LABELS,
        diagnose_direct_outcomes,
        predicate_clauses,
    )

    assert len(predicate_clauses()) == len(LABELS) == 14
    assert "mineagent_analysis_stopped" in predicate_clauses()[0]
    assert "research_budget_settlements" in predicate_clauses()[-1]
    events = []
    result = object()

    def publisher(*args):
        events.extend(("outcome_flushed", "full_settlement_flushed"))
        return result

    @contextmanager
    def nested():
        assert events[-1] == "full_settlement_flushed"
        events.append("savepoint")
        try:
            yield
        except Exception:
            events.append("rollback_savepoint")
            raise
        else:
            events.append("release_savepoint")

    monkeypatch.setattr(
        factor_analysis_results, "persist_direct_analysis_outcome", publisher
    )
    connection = SimpleNamespace(begin_nested=nested, scalar=lambda _: "test_schema")
    session = SimpleNamespace(connection=lambda: connection)
    for mode in ("all_pass", "full_charge", "missing", "query_error"):

        def execute(statement, params, mode=mode):
            assert events[-1] == "savepoint"
            assert '"test_schema".factor_direct_analysis_outcomes' in str(statement)
            assert "secret-analysis-id" not in str(statement)
            if mode == "query_error":
                from sqlalchemy.exc import DBAPIError

                raise DBAPIError(
                    "private SQL",
                    {"private_parameter": "secret"},
                    ValueError("private driver error"),
                )
            values = {label: label == mode for label in LABELS}
            rows = [] if mode == "missing" else [values]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

        connection.execute = execute
        with diagnose_direct_outcomes():
            assert (
                factor_analysis_results.persist_direct_analysis_outcome(
                    session, None, "secret-analysis-id", None, None
                )
                is result
            )
        output = capsys.readouterr().out
        expected = {
            "missing": "diagnostic_unavailable_row_inventory",
            "query_error": "diagnostic_unavailable",
        }.get(mode, mode)
        assert output == "DIRECT_ANALYSIS_PREDICATES:" + expected + "\n"
        assert "secret" not in output and "private" not in output
        assert events[-1] == (
            "rollback_savepoint" if mode == "query_error" else "release_savepoint"
        )
    # A real SQLAlchemy connection SAVEPOINT must not call the Session commit
    # veto, and a failed diagnostic SELECT must leave its outer txn usable.
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    from tests.integration import direct_analysis_diagnostic as diagnostic

    engine = create_engine("sqlite://")
    with Session(engine) as real_session, monkeypatch.context() as local:
        real_session.begin()

        def forbid_commit(session):
            raise AssertionError("neutral helper Session commit forbidden")

        event.listen(real_session, "before_commit", forbid_commit)

        def unavailable(connection, analysis_id):
            connection.execute(text("SELECT private_column FROM missing_private_table"))

        local.setattr(diagnostic, "observe_direct_outcome", unavailable)
        with diagnose_direct_outcomes():
            assert (
                factor_analysis_results.persist_direct_analysis_outcome(
                    real_session, None, None, None, None
                )
                is result
            )
        assert real_session.in_transaction()
        assert real_session.scalar(text("SELECT 1")) == 1
        assert (
            capsys.readouterr().out
            == "DIRECT_ANALYSIS_PREDICATES:diagnostic_unavailable\n"
        )
        event.remove(real_session, "before_commit", forbid_commit)
        real_session.rollback()
    engine.dispose()
    # Exact installed-body transformation changes only finite return labels.
    from backend.app.analysis_process_ddl import render_sql
    from tests.integration.direct_analysis_diagnostic import (
        STOP_LABELS,
        observe_stopped_detail,
        stopped_diagnostic_body,
    )

    installed = (
        render_sql("test_schema")
        .split("CREATE FUNCTION test_schema.mineagent_analysis_stopped(", 1)[1]
        .split("AS $$", 1)[1]
        .split("$$;", 1)[0]
    )
    installed = installed.replace("test_schema.", '"test_schema".')
    body = stopped_diagnostic_body("test_schema", installed)
    assert len(STOP_LABELS) == 15 and "RETURN false;" not in body
    assert "RETURN 'all_pass';" in body
    assert "SQLERRM" not in body and "RAISE" not in body
    with pytest.raises(AssertionError):
        stopped_diagnostic_body("test_schema", installed + "\n")
    for returned in ("all_pass", "observed_window", "exception_data", "private"):
        actions = []
        values = iter((installed, returned))
        fake = SimpleNamespace(
            begin_nested=lambda actions=actions: SimpleNamespace(
                rollback=lambda: actions.append("rollback")
            ),
            scalar=lambda *args, values=values: next(values),
        )

        def ddl(statement, actions=actions, **kwargs):
            assert statement.startswith("CREATE FUNCTION pg_temp.")
            assert kwargs == {"execution_options": {"no_parameters": True}}
            assert body in statement
            actions.append("create")

        fake.exec_driver_sql = ddl
        if returned == "private":
            with pytest.raises(AssertionError):
                observe_stopped_detail(fake, "private-id", "test_schema")
            assert capsys.readouterr().out == ""
        else:
            observe_stopped_detail(fake, "private-id", "test_schema")
            assert (
                capsys.readouterr().out == "DIRECT_ANALYSIS_STOPPED:" + returned + "\n"
            )
        assert actions == ["create", "rollback"]
    # Actual savepoint rollback removes diagnostic-only DDL and leaves outer txn.
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN")
        transaction = connection.begin_nested()
        try:
            connection.exec_driver_sql("CREATE TEMP TABLE diagnostic_only (id integer)")
            from sqlalchemy.exc import OperationalError

            with pytest.raises(OperationalError):
                connection.exec_driver_sql("SELECT missing FROM diagnostic_only")
        finally:
            transaction.rollback()
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM sqlite_temp_master WHERE name='diagnostic_only'"
                )
            )
            == 0
        )
        assert connection.in_transaction() and connection.scalar(text("SELECT 1")) == 1
        connection.rollback()
    engine.dispose()
    failure = ValueError("original publisher failure")

    def rejected(*args):
        raise failure

    monkeypatch.setattr(
        factor_analysis_results, "persist_direct_analysis_outcome", rejected
    )
    with diagnose_direct_outcomes(), pytest.raises(ValueError) as caught:
        factor_analysis_results.persist_direct_analysis_outcome(
            session, None, None, None, None
        )
    assert caught.value is failure and capsys.readouterr().out == ""
