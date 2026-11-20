"""Pure ticket scheduling; no claim of real PostgreSQL or native coverage."""

from dataclasses import replace
from threading import Event, Thread
from uuid import UUID

import pytest

from tests.sql_probes.rd_admission_expiry_observation import ExpiryTicket
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected
from tests.sql_probes.rd_ticketed_admission_candidate import TicketedAdmission
from tests.sql_probes.test_rd_admission_expiry_observation_unit import setup_observer


def setup_gate(monkeypatch):
    _, factory, fence, _ = setup_observer()
    state = {"now": 1, "cutoff": 20, "tickets": [], "observing": False}
    gate = TicketedAdmission(factory, fence, clock=lambda: state["now"])

    def observe(f, binding, *, role, clock):
        state["observing"] = True
        try:
            ticket = ExpiryTicket(f, binding, clock(), state["cutoff"], role)
            state["tickets"].append(ticket)
            return ticket
        finally:
            state["observing"] = False

    monkeypatch.setattr(gate._observer, "observe", observe)
    return gate, factory, fence, state


def launch(call):
    errors = []

    def run():
        try:
            call()
        except BaseException as error:  # noqa: BLE001 -- private captured test result
            errors.append(error)

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def join(thread):
    thread.join(2)
    if thread.is_alive():
        raise AssertionError("Candidate test thread did not finish")


@pytest.mark.parametrize("first_role", ["neutral", "heartbeat"])
def test_both_orders_hold_original_commit_scope(monkeypatch, first_role):
    gate, factory, fence, state = setup_gate(monkeypatch)
    waiting, committed, entered = Event(), Event(), Event()
    wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def contender():
        role = "neutral" if first_role == "heartbeat" else "heartbeat"
        with gate.transaction(factory, fence, role=role):
            assert committed.is_set() and not state["observing"]
            entered.set()

    with gate.transaction(factory, fence, role=first_role):
        assert not state["observing"]
        thread, errors = launch(contender)
        assert waiting.wait(1) and not entered.is_set()
        committed.set()
    join(thread)
    assert not errors and entered.is_set()
    assert len(state["tickets"]) == 2


def test_nested_and_binding_rejected_before_observation(monkeypatch):
    gate, factory, fence, state = setup_gate(monkeypatch)
    with gate.transaction(factory, fence, role="neutral"):
        with (
            pytest.raises(AdmissionRejected, match="nested_admission"),
            gate.transaction(factory, fence, role="heartbeat"),
        ):
            pass
        assert len(state["tickets"]) == 1 and gate._owner is not None
    with (
        pytest.raises(AdmissionRejected, match="binding_mismatch"),
        gate.transaction(
            factory, replace(fence, lease_token=UUID(int=9)), role="neutral"
        ),
    ):
        pass
    assert len(state["tickets"]) == 1


def test_expired_waiter_never_refreshes_but_later_ticket_can_renew(monkeypatch):
    gate, factory, fence, state = setup_gate(monkeypatch)
    waiting = Event()
    wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def contender():
        with gate.transaction(factory, fence, role="heartbeat"):
            raise AssertionError("Expired waiter entered")

    with gate.transaction(factory, fence, role="neutral"):
        thread, errors = launch(contender)
        assert waiting.wait(1)
        state["now"], state["cutoff"] = 21, 120
        with gate._condition:
            gate._condition.notify_all()
        join(thread)
    assert len(errors) == 1 and type(errors[0]) is AdmissionRejected
    assert str(errors[0]) == "expired"
    assert state["tickets"][1].cutoff == 20
    with gate.transaction(factory, fence, role="heartbeat"):
        assert state["tickets"][-1].cutoff == 120
    assert len(state["tickets"]) == 3


def test_close_cancels_waiter_not_holder(monkeypatch):
    gate, factory, fence, _state = setup_gate(monkeypatch)
    waiting = Event()
    wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def contender():
        with gate.transaction(factory, fence, role="heartbeat"):
            raise AssertionError("Closed gate entered")

    with gate.transaction(factory, fence, role="neutral"):
        thread, errors = launch(contender)
        assert waiting.wait(1)
        gate.close()
        join(thread)
        assert gate._owner is not None
    assert len(errors) == 1 and str(errors[0]) == "closed"
    assert not gate._participants and not gate._queue


@pytest.mark.parametrize("where", ["observation", "body", "wait"])
def test_cancellation_preserved_and_releases_owned_registration(monkeypatch, where):
    gate, factory, fence, _state = setup_gate(monkeypatch)
    failure = KeyboardInterrupt()

    def fail(*args, **kwargs):
        raise failure

    if where == "observation":
        monkeypatch.setattr(gate._observer, "observe", fail)
    elif where == "wait":
        gate._owner = object()
        monkeypatch.setattr(gate._condition, "wait", fail)
    with (
        pytest.raises(KeyboardInterrupt) as caught,
        gate.transaction(factory, fence, role="neutral"),
    ):
        raise failure
    assert caught.value is failure
    assert not gate._participants and not gate._queue
    if where != "wait":
        assert gate._owner is None


def test_fifo_completed_observations_cannot_overtake(monkeypatch):
    gate, factory, fence, _state = setup_gate(monkeypatch)
    waiting = Event()
    order = []
    wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def contender(number):
        with gate.transaction(factory, fence, role="neutral"):
            order.append(number)

    with gate.transaction(factory, fence, role="heartbeat"):
        first, first_errors = launch(lambda: contender(1))
        assert waiting.wait(1)
        waiting.clear()
        second, second_errors = launch(lambda: contender(2))
        assert waiting.wait(1)
    join(first)
    join(second)
    assert not first_errors and not second_errors
    assert order == [1, 2]


def test_wrong_thread_exit_closes_gate(monkeypatch):
    gate, factory, fence, _state = setup_gate(monkeypatch)
    context = gate.transaction(factory, fence, role="neutral")
    context.__enter__()
    thread, errors = launch(lambda: context.__exit__(None, None, None))
    join(thread)
    assert len(errors) == 1 and str(errors[0]) == "thread_mismatch"
    assert gate._closed and not gate._participants and gate._owner is None
