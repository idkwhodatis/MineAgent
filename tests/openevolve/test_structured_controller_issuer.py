"""Committed declaration/claim ordering with explicit process and DB doubles."""

from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from packages.factors.contracts import canonical_bytes
from packages.research_runtime.process_contracts import ProcessError
from workers.research import oe_structured_controller as issuer


def arranged(monkeypatch):
    calls = []
    state = NS(
        active=False,
        locked=False,
        fail_commit=False,
        serve_error=False,
        stop_error=False,
        short_lease=False,
    )
    fence = NS(attempt_id=uuid4())
    config = NS(runtime=NS(controller_image_id="sha256:" + "a" * 64))
    epoch = NS(id=uuid4())
    intent = NS(admission=NS(id=uuid4()))
    bootstrap, seed = canonical_bytes({"real": "declaration"}), b"seed"

    @contextmanager
    def guard(*a):
        assert not state.locked
        state.locked = True
        try:
            yield
        finally:
            state.locked = False

    @contextmanager
    def txn(*a, **kw):
        assert state.locked and not state.active
        state.active = True
        try:
            yield NS(
                get=lambda *a: NS(
                    lease_expires_at=utcnow()
                    + timedelta(seconds=1 if state.short_lease else 800)
                )
            )
            if state.fail_commit:
                raise ProcessError("commit_failed")
            calls.append("commit")
        finally:
            state.active = False

    def persist(*a):
        assert state.active and state.locked
        calls.append("intent")

    def claim(*a):
        assert state.active and state.locked
        calls.append("claim")
        return intent

    def check(*a, **kw):
        assert state.active and state.locked
        calls.append("fresh_check")

    def observed(*a, **kw):
        assert state.active and state.locked
        calls.append("identity")

    def record_stop(*a, **kw):
        assert state.active and state.locked
        calls.append("stopped")

    class Supervisor:
        def __init__(self, *a):
            pass

        def start(
            self,
            root,
            authority,
            boot,
            source,
            ticket,
            *,
            on_identity,
            before_submission,
        ):
            assert not state.active and state.locked
            assert (
                calls.index("intent")
                < calls.index("claim")
                < calls.index("fresh_check")
            )
            assert (boot, source) == (bootstrap, seed)
            before_submission()
            calls.append("start")
            on_identity(object())
            return running

    def finish(*, on_stopped, no_reader=False):
        assert state.locked and not state.active
        calls.append("contain" if no_reader else "finish")
        if state.stop_error:
            raise ProcessError("stop_unknown")
        on_stopped(object())

    running = NS(finish=finish)

    def serve(*a, **kw):
        assert not state.active and not state.locked
        assert kw["running"] is running
        calls.append("serve")
        if state.serve_error:
            raise ProcessError("event_failed")

    monkeypatch.setattr(issuer, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(issuer, "native_storage_guard", guard)
    monkeypatch.setattr(issuer, "neutral_transaction", txn)
    monkeypatch.setattr(issuer, "_current", lambda *a: (None, config))
    monkeypatch.setattr(issuer, "image_inventory", lambda *a: {})
    monkeypatch.setattr(
        issuer,
        "prepare_controller_epoch",
        lambda *a, **kw: (epoch, intent, bootstrap, seed),
    )
    monkeypatch.setattr(issuer, "persist_controller_intent", persist)
    monkeypatch.setattr(issuer, "claim_controller_intent", claim)
    monkeypatch.setattr(issuer, "check_controller_claim", check)
    monkeypatch.setattr(
        issuer,
        "expected_controller_bootstrap",
        lambda *a: (epoch, {"real": "declaration"}, seed),
    )
    monkeypatch.setattr(issuer, "_issue_committed_authority", lambda value: value)
    monkeypatch.setattr(issuer, "ControllerSupervisor", Supervisor)
    monkeypatch.setattr(issuer, "record_controller_identity", observed)
    monkeypatch.setattr(issuer, "record_controller_stopped", record_stop)
    monkeypatch.setattr(
        issuer,
        "record_controller_completion",
        lambda *a, **kw: calls.append("completion"),
    )
    monkeypatch.setattr(
        issuer, "settle_stopped_controller", lambda *a, **kw: calls.append("settled")
    )
    monkeypatch.setattr(issuer, "serve_controller", serve)

    def execute():
        return issuer.execute_controller_epoch(
            NS(kw={"bind": object()}), fence, NS(storage_root="/unused")
        )

    return NS(**locals())


def test_committed_one_shot_controller_then_actual_socket_loop_then_stop(monkeypatch):
    c = arranged(monkeypatch)
    assert c.execute() == c.epoch.id
    assert c.calls[-6:] == [
        "finish",
        "stopped",
        "settled",
        "commit",
        "completion",
        "commit",
    ]
    assert c.calls.count("start") == 1


def test_failed_first_commit_never_creates_controller(monkeypatch):
    c = arranged(monkeypatch)
    c.state.fail_commit = True
    with pytest.raises(ProcessError, match="commit_failed"):
        c.execute()
    assert "start" not in c.calls


def test_short_original_lease_is_not_silently_extended(monkeypatch):
    c = arranged(monkeypatch)
    c.state.short_lease = True
    with pytest.raises(ProcessError, match="original_lease"):
        c.execute()
    assert "intent" not in c.calls


def test_event_failure_can_contain_but_never_returns_success(monkeypatch):
    c = arranged(monkeypatch)
    c.state.serve_error = True
    with pytest.raises(ProcessError, match="event_failed"):
        c.execute()
    assert c.calls[-4:] == ["contain", "stopped", "settled", "commit"]
    assert c.calls.count("start") == 1


def test_unknown_stop_preserves_original_failure_without_settlement(monkeypatch):
    c = arranged(monkeypatch)
    c.state.serve_error = c.state.stop_error = True
    with pytest.raises(ProcessError, match="event_failed") as error:
        c.execute()
    assert str(error.value.__cause__) == "stop_unknown"
    assert "settled" not in c.calls
