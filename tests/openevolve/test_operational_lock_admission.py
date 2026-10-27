"""Acquisition-only retry; transaction doubles plus actual SQLAlchemy veto tests."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import replace
from uuid import uuid4

import pytest

# Keep exception boundaries visibly distinct in lifecycle tests.
# ruff: noqa: SIM117
from backend.app import openevolve_lock_admission as window
from backend.app import research_lifecycle as lifecycle
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence
from tests.research_runtime.test_neutral_terminal_commit import arranged


def fence():
    return AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())


def test_scope_threads_and_finally_reset():
    f = fence()
    with window.acquisition_window(f, 100):
        with window.acquisition_window(f, 200):
            assert window.current_window(f).deadline == 100
        for foreign in (
            replace(f, generation=2),
            replace(f, lease_token=uuid4()),
            fence(),
        ):
            with pytest.raises(DomainError, match="scope unavailable"):
                window.current_window(foreign)
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(window.current_window, f).result() is None
            copied = copy_context()
            with pytest.raises(DomainError):
                pool.submit(copied.run, window.current_window, f).result()
        with pytest.raises(KeyboardInterrupt), window.acquisition_window(f, 50):
            raise KeyboardInterrupt
        assert window.current_window(f).deadline == 100
    assert window.current_window(f) is None
    import socket

    from tests.openevolve.test_operational_socket_server import initial

    unit = initial()
    unit.fence = object()
    client, host = socket.socketpair()
    try:
        with pytest.raises(DomainError):
            unit._handle(host, "events")
        assert host.fileno() == -1
    finally:
        client.close()
        host.close()


def test_retry_only_enter_after_close_and_fixed_deadlines(monkeypatch):
    f = fence()
    clock, calls = [10.0], []
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])

    def sleep(delay):
        assert calls[-1] == "closed"
        clock[0] += delay

    monkeypatch.setattr(window.time, "sleep", sleep)

    @contextmanager
    def once(*args, **kwargs):
        calls.append("enter")
        try:
            raise lifecycle._neutral_error("neutral_busy")
            yield
        finally:
            calls.append("closed")

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", once)
    with pytest.raises(DomainError), lifecycle.neutral_transaction(None, f):
        pytest.fail("not acquired")
    assert calls == ["enter", "closed"]
    for original, expected in ((11.0, 11.0), (100.0, 15.0)):
        clock[0] = 10.0
        with window.acquisition_window(f, original), pytest.raises(DomainError):
            with lifecycle.neutral_transaction(None, f):
                pytest.fail("not acquired")
        assert clock[0] == pytest.approx(expected)


@pytest.mark.parametrize("failure", ["body", "commit", "cancel", "other_enter"])
def test_post_yield_commit_and_cancellation_never_retry(monkeypatch, failure):
    f, calls = fence(), []

    @contextmanager
    def once(*args, **kwargs):
        calls.append("enter")
        try:
            if failure == "other_enter":
                raise lifecycle._neutral_error("neutral_fence_lost")
            yield object()
            raise lifecycle._neutral_error("neutral_busy")
        finally:
            calls.append("closed")

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", once)
    with window.acquisition_window(f, window.time.monotonic() + 60):
        with pytest.raises((DomainError, KeyboardInterrupt)):
            with lifecycle.neutral_transaction(None, f):
                calls.append("body")
                if failure == "body":
                    raise lifecycle._neutral_error("neutral_busy")
                if failure == "cancel":
                    raise KeyboardInterrupt
    assert calls.count("enter") == 1
    assert calls.count("body") == (0 if failure == "other_enter" else 1)
    assert calls[-1] == "closed"


def test_actual_session_retry_revalidates_fence_and_body_once(monkeypatch):
    factory, f, _run, attempt, *rest = arranged(monkeypatch)
    calls, sessions = [], []

    def lock(session, scope):
        sessions.append(session)
        calls.append("lock")
        if len(calls) == 1:
            raise lifecycle._neutral_error("neutral_busy")

    monkeypatch.setattr(lifecycle, "_try_neutral_lock", lock)
    with window.acquisition_window(f, window.time.monotonic() + 5):
        with lifecycle.neutral_transaction(factory, f):
            calls.append("body")
    assert calls.count("body") == 1
    assert sessions[0] is not sessions[1]
    assert not sessions[0].in_transaction()
    assert rest[-1] == [True]

    calls.clear()

    def sleep(_):
        assert not sessions[-1].in_transaction()
        attempt.lease_token = uuid4()

    monkeypatch.setattr(window.time, "sleep", sleep)
    with (
        window.acquisition_window(f, window.time.monotonic() + 5),
        pytest.raises(DomainError),
    ):
        with lifecycle.neutral_transaction(factory, f):
            pytest.fail("revoked fence must be reread")


def test_expired_foreign_and_late_acquisition_do_not_yield(monkeypatch):
    f, calls, clock = fence(), [], [10.0]
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])

    @contextmanager
    def once(*args, **kwargs):
        calls.append("enter")
        clock[0] = 20.0
        try:
            yield object()
            calls.append("commit")
        finally:
            calls.append("closed")

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", once)
    with window.acquisition_window(f, 9), pytest.raises(DomainError):
        with lifecycle.neutral_transaction(None, f):
            pytest.fail("expired")
    with window.acquisition_window(f, 15), pytest.raises(DomainError):
        with lifecycle.neutral_transaction(None, replace(f, generation=2)):
            pytest.fail("foreign")
    assert not calls
    with window.acquisition_window(f, 15), pytest.raises(DomainError):
        with lifecycle.neutral_transaction(None, f):
            pytest.fail("late")
    assert calls == ["enter", "closed"]


def test_actual_commit_failure_is_not_reentered(monkeypatch):
    from sqlalchemy import event

    factory, f, *_ = arranged(monkeypatch)
    entered = []

    def veto(_):
        raise lifecycle._neutral_error("neutral_busy")

    event.listen(factory.class_, "before_commit", veto)
    try:
        with (
            window.acquisition_window(f, window.time.monotonic() + 5),
            pytest.raises(DomainError),
        ):
            with lifecycle.neutral_transaction(factory, f):
                entered.append(True)
    finally:
        event.remove(factory.class_, "before_commit", veto)
    assert entered == [True]
