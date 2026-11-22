"""Wrapper mechanics only; real native workflow remains a separate required proof."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app import research_lifecycle as lifecycle
from backend.app import services
from packages.research_runtime.fences import AttemptFence
from tests.sql_probes import rd_native_admission_trial as trial
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected
from workers.research import lease as lease_module
from workers.research import rdagent_queue
from workers.research.lease import AttemptLease


def setup_trial(monkeypatch):
    state = {
        "events": [],
        "gates": [],
        "fail": None,
        "alive": False,
        "heartbeat": lease_module.heartbeat,
        "ticket": None,
    }
    factory = sessionmaker()
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))

    class Gate:
        def __init__(self, f, binding):
            assert f is factory and binding == fence
            state["gates"].append(self)

        @contextmanager
        def transaction(self, f, binding, *, role):
            state["events"].append("enter:" + role)
            if state["fail"] is not None:
                raise state["fail"]
            try:
                yield state["ticket"]
            finally:
                state["events"].append("exit:" + role)

        def close(self):
            state["events"].append("gate_close")

    def claim(*args, **kwargs):
        state["events"].append("original_claim")
        return fence

    @contextmanager
    def neutral(*args, **kwargs):
        state["events"].append("original_neutral")
        yield "session"
        state["events"].append("original_commit")

    def pulse(lease, force=False):
        state["events"].append("original_pulse_commit")
        return "result"

    def close(lease):
        state["events"].append("original_close")

    monkeypatch.setattr(trial, "TicketedAdmission", Gate)
    monkeypatch.setattr(rdagent_queue, "claim_configured_run", claim)
    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", neutral)
    monkeypatch.setattr(AttemptLease, "pulse", pulse)
    monkeypatch.setattr(AttemptLease, "close", close)
    lease = AttemptLease(
        factory,
        SimpleNamespace(),
        fence.attempt_id,
        fence.generation,
        fence.lease_token,
        "test",
    )
    lease.thread = SimpleNamespace(is_alive=lambda: state["alive"])
    return (
        state,
        factory,
        fence,
        lease,
        (claim, neutral, pulse, close, AttemptLease.start),
    )


def test_original_claim_then_whole_neutral_and_pulse_then_restore(monkeypatch):
    state, factory, fence, lease, originals = setup_trial(monkeypatch)
    with trial.native_admission_trial() as counts:
        assert rdagent_queue.claim_configured_run(factory) == fence
        with lifecycle._neutral_transaction_once(factory, fence) as session:
            assert session == "session"
            state["events"].append("body")
        assert lease.pulse(force=True) == "result"
        lease.close()
    assert state["events"][:8] == [
        "original_claim",
        "enter:neutral",
        "original_neutral",
        "body",
        "original_commit",
        "exit:neutral",
        "enter:heartbeat",
        "original_pulse_commit",
    ]
    assert state["events"].index("gate_close") < state["events"].index("original_close")
    assert counts == {
        "claims": 1,
        "neutral_commits": 1,
        "pulse_returns": 1,
        "owner_first_pulses": 0,
    }
    assert lease_module.heartbeat is state["heartbeat"]
    assert (
        rdagent_queue.claim_configured_run,
        lifecycle._neutral_transaction_once,
        AttemptLease.pulse,
        AttemptLease.close,
        AttemptLease.start,
    ) == originals


def test_scheduling_failure_revokes_pulse_and_preserves_exception(monkeypatch):
    state, factory, _fence, lease, _ = setup_trial(monkeypatch)
    failure = AdmissionRejected("expired")
    with trial.native_admission_trial():
        rdagent_queue.claim_configured_run(factory)
        state["fail"] = failure
        with pytest.raises(AdmissionRejected) as caught:
            lease.pulse(force=True)
        assert caught.value is failure
        assert lease.lost.is_set() and lease.cancelled.is_set()
        assert "original_pulse_commit" not in state["events"]


def test_wrong_factory_passthrough_but_wrong_fence_rejected(monkeypatch):
    _state, factory, fence, lease, _ = setup_trial(monkeypatch)
    with trial.native_admission_trial() as counts:
        rdagent_queue.claim_configured_run(factory)
        with lifecycle._neutral_transaction_once(sessionmaker(), fence):
            pass
        with (
            pytest.raises(AdmissionRejected, match="binding_mismatch"),
            lifecycle._neutral_transaction_once(factory, None),
        ):
            pass
        assert counts["neutral_commits"] == 0
        lease.token = UUID(int=90)
        with pytest.raises(AdmissionRejected, match="lease_binding_mismatch"):
            lease.pulse()
        assert lease.lost.is_set() and lease.cancelled.is_set()


def test_body_failure_restores_after_owned_thread_cleanup(monkeypatch):
    state, factory, _fence, lease, originals = setup_trial(monkeypatch)
    failure = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, trial.native_admission_trial():
        rdagent_queue.claim_configured_run(factory)
        lease.pulse()
        raise failure
    assert caught.value is failure
    assert AttemptLease.pulse is originals[2]
    assert "gate_close" in state["events"] and "original_close" in state["events"]


def test_live_thread_fails_red_and_does_not_restore_under_thread(monkeypatch):
    state, factory, _fence, lease, originals = setup_trial(monkeypatch)
    try:
        with (
            pytest.raises(AdmissionRejected, match="trial_cleanup_thread_alive"),
            trial.native_admission_trial(),
        ):
            rdagent_queue.claim_configured_run(factory)
            lease.pulse()
            state["alive"] = True
            with pytest.raises(AdmissionRejected, match="lease_thread_still_alive"):
                lease.close()
        assert AttemptLease.pulse is not originals[2]
    finally:
        # Fake thread only. Explicitly reset these test-owned patches so this unit
        # suite can continue; actual native trial process must terminate instead.
        state["alive"] = False
        (
            rdagent_queue.claim_configured_run,
            lifecycle._neutral_transaction_once,
            AttemptLease.pulse,
            AttemptLease.close,
            AttemptLease.start,
        ) = originals
        lease_module.heartbeat = state["heartbeat"]


@pytest.mark.parametrize(
    ("existing", "remaining", "timeout"),
    [("0", 0.21575, "215ms"), ("0", 8, "5000ms"), ("15ms", 8, "15ms")],
)
def test_owner_lock_limits_only_original_fenced_pulse_transaction(
    monkeypatch, existing, remaining, timeout
):
    state, factory, fence, lease, _originals = setup_trial(monkeypatch)
    bind = object()
    factory.configure(bind=bind)

    def execute(statement, params=None):
        if params is None:
            return SimpleNamespace(one=lambda: (existing, 0 if existing == "0" else 15))
        state["events"].append(("timeout", params["timeout"]))

    session = SimpleNamespace(
        execute=execute,
        get_bind=lambda: bind,
        in_transaction=lambda: True,
        in_nested_transaction=lambda: False,
    )

    def check_remaining(f, attempt, *, role):
        assert (f, attempt, role) == (factory, fence, "heartbeat")
        state["events"].append("expiry")
        return remaining

    state["ticket"] = SimpleNamespace(remaining=check_remaining)

    def owner(s, key):
        assert s is session
        assert key == f"submit:{fence.owner_id}"
        state["events"].append("owner_lock")

    def original_heartbeat(s, attempt, generation, token, settings):
        assert s is session
        state["events"].append("original_fenced_heartbeat")
        return True

    def original_pulse(current, force=False):
        assert lease_module.heartbeat(
            session,
            current.attempt_id,
            current.generation,
            current.token,
            current.settings,
        )
        state["events"].append("original_pulse_commit")

    monkeypatch.setattr(services, "advisory_lock", owner)
    monkeypatch.setattr(lease_module, "heartbeat", original_heartbeat)
    monkeypatch.setattr(AttemptLease, "pulse", original_pulse)
    with trial.native_admission_trial() as counts:
        # An otherwise identical direct call outside the trial's pulse scope is
        # not enrolled, even after this exact factory/fence is claimed.
        rdagent_queue.claim_configured_run(factory)
        lease_module.heartbeat(
            session, lease.attempt_id, lease.generation, lease.token, lease.settings
        )
        lease.pulse(force=True)

    assert state["events"][:11] == [
        "original_claim",
        "original_fenced_heartbeat",
        "enter:heartbeat",
        "expiry",
        ("timeout", timeout),
        "owner_lock",
        ("timeout", existing),
        "expiry",
        "original_fenced_heartbeat",
        "original_pulse_commit",
        "exit:heartbeat",
    ]
    assert counts["owner_first_pulses"] == counts["pulse_returns"] == 1
    assert lease_module.heartbeat is original_heartbeat


@pytest.mark.parametrize("boundary", ("before", "after"))
def test_expired_original_ticket_never_reaches_heartbeat(monkeypatch, boundary):
    _state, factory, fence, _lease, _originals = setup_trial(monkeypatch)
    bind = object()
    factory.configure(bind=bind)
    calls = []
    countdown = iter([0.001] if boundary == "after" else [])

    def remaining(*args, **kwargs):
        result = next(countdown, None)
        if result is None:
            raise AdmissionRejected("expired")
        return result

    session = SimpleNamespace(
        execute=lambda *_args: SimpleNamespace(one=lambda: ("0", 0)),
        get_bind=lambda: bind,
        in_transaction=lambda: True,
        in_nested_transaction=lambda: False,
    )
    monkeypatch.setattr(services, "advisory_lock", lambda *_: calls.append("owner"))
    with pytest.raises(AdmissionRejected, match="expired"):
        trial._lock_heartbeat_owner(
            session, factory, fence, SimpleNamespace(remaining=remaining)
        )
    assert calls == (["owner"] if boundary == "after" else [])


def test_nested_context_preserves_original_rejection_before_gate(monkeypatch):
    state, factory, fence, _lease, _originals = setup_trial(monkeypatch)
    failure = AdmissionRejected("original_nested_rejection")

    @contextmanager
    def reject_nested(*args, **kwargs):
        if lifecycle._neutral_scope.get() is not None:
            raise failure
        yield

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", reject_nested)
    with trial.native_admission_trial():
        rdagent_queue.claim_configured_run(factory)
        token = lifecycle._neutral_scope.set({"test": True})
        try:
            with (
                pytest.raises(AdmissionRejected) as caught,
                lifecycle._neutral_transaction_once(factory, fence),
            ):
                pass
            assert caught.value is failure
            assert "enter:neutral" not in state["events"]
        finally:
            lifecycle._neutral_scope.reset(token)


@pytest.mark.parametrize("failure", [RuntimeError("start failed"), KeyboardInterrupt()])
def test_started_thread_tracked_before_original_start_raises(monkeypatch, failure):
    state, factory, _fence, lease, _originals = setup_trial(monkeypatch)

    def start(current):
        assert current is lease
        state["alive"] = True
        raise failure

    def close(current):
        assert current is lease
        state["events"].append("original_close")
        state["alive"] = False

    monkeypatch.setattr(AttemptLease, "start", start)
    monkeypatch.setattr(AttemptLease, "close", close)
    with pytest.raises(type(failure)) as caught, trial.native_admission_trial():
        rdagent_queue.claim_configured_run(factory)
        lease.start()
    assert caught.value is failure
    assert "gate_close" in state["events"]
    assert "original_close" in state["events"]
    assert state["alive"] is False
    assert AttemptLease.start is start


def test_registered_start_is_rejected_during_scope_cleanup(monkeypatch):
    state, factory, _fence, lease, _originals = setup_trial(monkeypatch)
    calls = []

    def start(current):
        calls.append(current)

    def close(current):
        with pytest.raises(AdmissionRejected, match="trial_closing"):
            current.start()
        state["events"].append("closing_rejected")

    monkeypatch.setattr(AttemptLease, "start", start)
    monkeypatch.setattr(AttemptLease, "close", close)
    with trial.native_admission_trial():
        rdagent_queue.claim_configured_run(factory)
        lease.start()
    assert len(calls) == 1
    assert "closing_rejected" in state["events"]
    assert AttemptLease.start is start


def test_metrics_output_is_bounded_and_drops_unrecognized_private_fields(capsys):
    trial.report_trial_counts(
        {
            "claims": 10**20,
            "neutral_commits": 2,
            "pulse_returns": 3,
            "owner_first_pulses": 1,
            "private": "secret-fence-token",
        }
    )
    output = capsys.readouterr().out
    assert "secret" not in output and "private" not in output
    assert "1000000" in output and "100000000000000000000" not in output
    trial.report_trial_counts(
        {"claims": "secret", "neutral_commits": 2, "pulse_returns": 3}
    )
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "print_error", [OSError("private output error"), KeyboardInterrupt()]
)
@pytest.mark.parametrize("baseline_fails", [False, True])
def test_native_finally_metrics_cannot_replace_original_outcome(
    monkeypatch, print_error, baseline_fails
):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "integration")
    )
    module = importlib.import_module("test_workflow_ticketed_admission_trial")
    baseline_failure = RuntimeError("private original failure")
    calls = []

    @contextmanager
    def context():
        yield {
            "claims": 1,
            "neutral_commits": 2,
            "pulse_returns": 3,
            "owner_first_pulses": 1,
        }

    def baseline(*args):
        calls.append("baseline")
        if baseline_fails:
            raise baseline_failure

    def broken_print(*args, **kwargs):
        calls.append("print")
        raise print_error

    monkeypatch.setenv("MINEAGENT_TICKETED_NATIVE_TRIAL_REQUIRED", "1")
    monkeypatch.setattr(module, "native_admission_trial", context)
    monkeypatch.setattr(module, "_original_baseline", baseline)
    monkeypatch.setattr("builtins.print", broken_print)
    if baseline_fails:
        with pytest.raises(RuntimeError) as caught:
            module.test_actual_current032g_workflow_with_ticketed_admission_trial(
                None, None, monkeypatch
            )
        assert caught.value is baseline_failure
    else:
        module.test_actual_current032g_workflow_with_ticketed_admission_trial(
            None, None, monkeypatch
        )
    assert calls == ["baseline", "print"]
