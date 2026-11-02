"""Actual shared state helpers with explicit DB doubles; no SDK or PG claim."""

import ast
from pathlib import Path

import pytest

from backend.app import oe_structured_provider_broker as broker
from backend.app import research_broker_services as neutral
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_admission import setup
from tests.openevolve.test_structured_provider_broker import arranged, settlement_case


def test_actual_admission_flushes_reserved_then_marks_uncertain_before_return(
    monkeypatch,
):
    case = setup(monkeypatch)
    _, _, transport = case.call()
    assert transport.state == "submitted"
    assert case.states[-3:] == [
        ("reserved", ("reserved",)),
        ("uncertain", ("reserved",)),
        ("uncertain", ("submitted",)),
    ]
    assert case.events[-1] == "require"


def test_unknown_or_reserved_hold_cannot_be_settled_as_submitted(monkeypatch):
    for state in ("reserved", "settled", "unknown", None):
        case = settlement_case(monkeypatch)
        case.reservation.state = state
        with pytest.raises(ProcessError):
            broker._settle(case.session, case.fence, case.membership.id, None)
        assert not case.charges and not case.added


def test_mark_failure_never_returns_an_admitted_provider_transport(monkeypatch):
    case = setup(monkeypatch)
    failure = RuntimeError("closed transition test failure")

    def fail(*args):
        raise failure

    monkeypatch.setattr(neutral, "mark_reservation_uncertain", fail)
    with pytest.raises(RuntimeError) as caught:
        case.call()
    assert caught.value is failure
    assert case.states[-1] == ("reserved", ("reserved",))
    assert ("uncertain", ("submitted",)) not in case.states


def test_admission_transaction_failure_never_reaches_sdk_or_physical_receipts(
    monkeypatch,
):
    case = arranged(monkeypatch)
    failure = RuntimeError("closed transition test failure")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(broker, "admit_provider_transport", fail)
    with pytest.raises(RuntimeError) as caught:
        case.call()
    assert caught.value is failure
    assert "rollback" in case.events
    assert "sdk" not in case.events and not case.documents


def test_original_immediate_and_deferred_sql_require_the_same_state_sequence():
    root = Path(__file__).parents[2]
    tree = ast.parse(
        (root / "backend/alembic/versions/015_neutral_primitives.py").read_text()
    )
    sql = next(
        n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BUDGET_SQL"
    )
    assert "Neutral initial state is invalid" in sql
    assert "to_jsonb(NEW)->>'state' <> 'reserved'" in sql
    assert "(t.state='submitted' AND (r.state<>'uncertain'" in sql
    assert "previous = 'reserved' AND following IN ('submitted','failed')" in sql
