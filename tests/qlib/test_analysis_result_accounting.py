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


def test_exact_full_settlement_allows_cached_reread():
    session, fence, receipt, row = fixture("settled")
    session.scalar = lambda *args: SimpleNamespace(
        charge=row.amounts, outcome="conservative_unknown"
    )
    assert _reservation(session, fence, receipt, completed=True)[0] is row
