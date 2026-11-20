"""Pure local scheduling proof, not a PostgreSQL/native coordination proof."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread
from time import monotonic
from uuid import UUID

import pytest

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes.rd_attempt_admission_candidate import (
    AdmissionRejected,
    AttemptAdmission,
)


def make_gate(*, clock=None, seconds=10):
    factory = object()
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    started = monotonic()
    wall = datetime.now(UTC)
    options = {} if clock is None else {"clock": clock}
    gate = AttemptAdmission(
        factory,
        fence,
        lease_expires_at=wall + timedelta(seconds=seconds),
        observation_started_utc=wall,
        observation_started_monotonic=started,
        **options,
    )
    return (gate, factory, fence)


def launch(function, name):
    errors = []

    def run():
        try:
            function()
        except BaseException as error:  # noqa: BLE001 -- surface thread failures in caller
            errors.append(error)

    thread = Thread(target=run, name=name, daemon=True)
    thread.start()
    return (thread, errors)


def finish(thread, errors):
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == []


@pytest.mark.parametrize("first_role", ["neutral", "heartbeat"])
def test_both_orders_hold_gate_through_commit_and_release_on_body_exception(
    monkeypatch, first_role
):
    gate, factory, fence = make_gate()
    waiting, committed, entered = (Event(), Event(), Event())
    original_wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return original_wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)
    failure = RuntimeError("body failure")

    def second():
        role = "heartbeat" if first_role == "neutral" else "neutral"
        with gate.transaction(factory, fence, role=role):
            assert committed.is_set()
            entered.set()

    with (
        pytest.raises(RuntimeError) as caught,
        gate.transaction(factory, fence, role=first_role),
    ):
        thread, errors = launch(second, "second")
        assert waiting.wait(1)
        assert not entered.is_set()
        # Model deferred checks and commit inside the protected region.
        committed.set()
        raise failure
    assert caught.value is failure
    finish(thread, errors)
    assert entered.is_set()


def test_fifo_waiters_cannot_be_overtaken(monkeypatch):
    gate, factory, fence = make_gate()
    waiting = {name: Event() for name in ("second", "third")}
    order = []
    original_wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting[current_thread().name].set()
        return original_wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def caller():
        with gate.transaction(factory, fence, role="neutral"):
            order.append(current_thread().name)

    with gate.transaction(factory, fence, role="heartbeat"):
        second = launch(caller, "second")
        assert waiting["second"].wait(1)
        third = launch(caller, "third")
        assert waiting["third"].wait(1)
    finish(*second)
    finish(*third)
    assert order == ["second", "third"]


def test_exact_binding_role_and_nested_admission_fail_closed():
    gate, factory, fence = make_gate()
    for supplied_factory, supplied_fence in (
        (object(), fence),
        (factory, replace(fence, generation=2)),
        (factory, replace(fence, lease_token=UUID(int=5))),
        (factory, replace(fence, owner_id=UUID(int=5))),
        (factory, replace(fence, run_id=UUID(int=5))),
        (factory, replace(fence, attempt_id=UUID(int=5))),
    ):
        with (
            pytest.raises(AdmissionRejected, match="binding_mismatch"),
            gate.transaction(supplied_factory, supplied_fence, role="neutral"),
        ):
            pytest.fail("Mismatched binding admitted")
    with (
        pytest.raises(AdmissionRejected, match="invalid_role"),
        gate.transaction(factory, fence, role="other"),
    ):
        pytest.fail("Unknown role admitted")
    with (
        gate.transaction(factory, fence, role="neutral"),
        pytest.raises(AdmissionRejected, match="nested_admission"),
        gate.transaction(factory, fence, role="heartbeat"),
    ):
        pytest.fail("Nested admission allowed")
    with gate.transaction(factory, fence, role="heartbeat"):
        pass


def test_close_rejects_waiter_without_interrupting_active_transaction(monkeypatch):
    gate, factory, fence = make_gate()
    waiting, rejected = (Event(), Event())
    original_wait = gate._condition.wait

    def observed_wait(*, timeout):
        waiting.set()
        return original_wait(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", observed_wait)

    def caller():
        with (
            pytest.raises(AdmissionRejected, match="closed"),
            gate.transaction(factory, fence, role="heartbeat"),
        ):
            pytest.fail("Closed scope admitted waiter")
        rejected.set()

    with gate.transaction(factory, fence, role="neutral"):
        thread, errors = launch(caller, "waiting")
        assert waiting.wait(1)
        gate.close()
        assert rejected.wait(1)
        assert gate._owner is not None
    finish(thread, errors)
    with (
        pytest.raises(AdmissionRejected, match="closed"),
        gate.transaction(factory, fence, role="neutral"),
    ):
        pytest.fail("Closed scope admitted caller")


def test_wait_expiry_cleans_ticket_without_refreshing_cutoff(monkeypatch):
    gate, factory, fence = make_gate()
    original_cutoff = gate._cutoff
    waiting, expired = (Event(), Event())
    clock = [gate._last_clock]
    gate._clock = lambda: clock[0]
    original_wait = gate._condition.wait

    def expire_wait(*, timeout):
        assert timeout > 0
        clock[0] = original_cutoff
        waiting.set()
        return original_wait(timeout=0)

    monkeypatch.setattr(gate._condition, "wait", expire_wait)

    def caller():
        with (
            pytest.raises(AdmissionRejected, match="expired"),
            gate.transaction(factory, fence, role="heartbeat"),
        ):
            pytest.fail("Expired wait admitted")
        expired.set()

    with gate.transaction(factory, fence, role="neutral"):
        thread, errors = launch(caller, "waiting")
        assert waiting.wait(1) and expired.wait(1)
    finish(thread, errors)
    assert gate._cutoff == original_cutoff
    assert not gate._queue and (not gate._participants) and (gate._owner is None)
    with (
        pytest.raises(AdmissionRejected, match="expired"),
        gate.transaction(factory, fence, role="neutral"),
    ):
        pytest.fail("Expiry was refreshed")


def test_expired_observation_and_invalid_clock_never_enter():
    gate, factory, fence = make_gate(seconds=-1)
    with (
        pytest.raises(AdmissionRejected, match="expired"),
        gate.transaction(factory, fence, role="neutral"),
    ):
        pytest.fail("Expired observation admitted")
    for clock in (lambda: float("nan"), lambda: True, lambda: -1):
        gate, factory, fence = make_gate(clock=clock)
        with (
            pytest.raises(AdmissionRejected, match="invalid_clock"),
            gate.transaction(factory, fence, role="neutral"),
        ):
            pytest.fail("Invalid clock admitted")
        assert not gate._participants and (not gate._queue)


def test_interruption_during_wait_removes_ticket_and_preserves_exception(monkeypatch):
    gate, factory, fence = make_gate()
    interrupted = KeyboardInterrupt("wait interrupted")

    def broken_wait(*, timeout):
        raise interrupted

    monkeypatch.setattr(gate._condition, "wait", broken_wait)

    def caller():
        with (
            pytest.raises(KeyboardInterrupt) as caught,
            gate.transaction(factory, fence, role="heartbeat"),
        ):
            pytest.fail("Interrupted wait admitted")
        assert caught.value is interrupted

    with gate.transaction(factory, fence, role="neutral"):
        thread, errors = launch(caller, "waiting")
        finish(thread, errors)
        assert not gate._queue and len(gate._participants) == 1
    with gate.transaction(factory, fence, role="heartbeat"):
        pass


def test_observation_read_time_consumes_original_expiry_without_refresh():
    wall = datetime(2026, 1, 1, tzinfo=UTC)
    factory = object()
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    now = [106.0]  # Six seconds already elapsed since clocks were sampled.
    gate = AttemptAdmission(
        factory,
        fence,
        lease_expires_at=wall + timedelta(seconds=10),
        observation_started_utc=wall,
        observation_started_monotonic=100.0,
        clock=lambda: now[0],
    )
    assert gate._cutoff == 110.0
    with gate.transaction(factory, fence, role="heartbeat"):
        pass
    now[0] = 110.0
    with (
        pytest.raises(AdmissionRejected, match="expired"),
        gate.transaction(factory, fence, role="neutral"),
    ):
        pytest.fail("A successful admission refreshed expiry")
    assert gate._cutoff == 110.0


def test_clock_error_is_closed_and_does_not_leak_or_poison_queue():
    def broken():
        raise RuntimeError("PRIVATE_CLOCK_DETAILS")

    gate, factory, fence = make_gate(clock=broken)
    with (
        pytest.raises(AdmissionRejected) as caught,
        gate.transaction(factory, fence, role="neutral"),
    ):
        pytest.fail("Failed clock admitted caller")
    assert str(caught.value) == "clock_unavailable"
    assert not gate._queue and not gate._participants and gate._owner is None


def test_candidate_has_no_database_runtime_hook_or_lease_authority():
    import ast
    from pathlib import Path

    from tests.sql_probes import rd_attempt_admission_candidate as candidate

    source = Path(candidate.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "execute",
                "exec_driver_sql",
                "connect",
                "begin",
                "commit",
                "rollback",
                "pulse",
                "start",
                "set",
                "setattr",
                "sleep",
            }
    root = Path(candidate.__file__).resolve().parents[2]
    for directory in (root / "backend", root / "workers", root / "runtimes"):
        for path in directory.rglob("*.py"):
            assert "rd_attempt_admission_candidate" not in path.read_text()


def test_wrong_thread_exit_closes_gate_and_never_transfers_admission():
    gate, factory, fence = make_gate()
    context = gate.transaction(factory, fence, role="neutral")
    context.__enter__()

    def wrong_thread():
        with pytest.raises(AdmissionRejected, match="thread_mismatch"):
            context.__exit__(None, None, None)

    thread, errors = launch(wrong_thread, "wrong-thread")
    finish(thread, errors)
    assert gate._closed and gate._owner is None
    assert not gate._participants and not gate._queue
    with (
        pytest.raises(AdmissionRejected, match="closed"),
        gate.transaction(factory, fence, role="heartbeat"),
    ):
        pytest.fail("Thread transfer reopened admission")
