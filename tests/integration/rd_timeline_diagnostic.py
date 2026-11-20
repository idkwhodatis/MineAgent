"""Test-only paired monotonic observations, never scheduling or authority.

Events are bounded and best effort. Commit events mean SQLAlchemy after_commit,
not a new lease-authority assertion. No identifiers or exception text are kept.
"""

import json
from collections import deque
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch

from rd_lease_diagnostic import _clock, _database_guard, _elapsed


@contextmanager
def diagnose_rd_timeline(factory, run_id=None):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from backend.app.research_lifecycle import _NEUTRAL_KEY
    from workers.research import rdagent_operational as operational
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease

    origin = _clock()
    role = ContextVar("rd_timeline_role", default=None)
    lock = Lock()
    maximum = 1024
    events = deque(maxlen=maximum)
    dropped = 0
    factories = [factory]

    def record(name, error=None, **fields):
        nonlocal dropped
        try:
            timestamp = _elapsed(origin, _clock())
            guard = "none" if error is None else _database_guard(error)
            if not lock.acquire(blocking=False):
                return
            try:
                if len(events) == maximum:
                    dropped = min(1_000_000, dropped + 1)
                events.append(
                    {"event": name, "at_ms": timestamp, "guard": guard, **fields}
                )
            finally:
                lock.release()
        except BaseException:  # noqa: BLE001, S110 -- never change original outcome
            pass

    def committed(session):
        try:
            # Dedicated fixture engine only; do not report another test's session.
            if session.in_nested_transaction() or not any(
                session.get_bind() is item.kw["bind"] for item in factories
            ):
                return
            current = role.get()
            if current in {"pulse", "recovery"}:
                record(current + "_db_commit")
            state = session.info.get(_NEUTRAL_KEY)
            if type(state) is dict and state.get("terminal") is not None:
                record("terminal_db_commit")
        except BaseException:  # noqa: BLE001, S110 -- listener is observational
            pass

    original_claim = rdagent_queue.claim_configured_run

    def claim(bound_factory, claimed_run_id, *args, **kwargs):
        # Queue execution creates a new engine/factory, distinct from the fixture.
        if run_id is not None and claimed_run_id == run_id and len(factories) == 1:
            factories.append(bound_factory)
        return original_claim(bound_factory, claimed_run_id, *args, **kwargs)

    original_pulse = AttemptLease.pulse
    original_recovery = operational._recover_and_record
    original_serve = operational.serve_one

    def pulse(lease, *args, **kwargs):
        if not any(lease.factory is item for item in factories):
            return original_pulse(lease, *args, **kwargs)
        token = role.set("pulse")
        record("pulse_enter")
        try:
            result = original_pulse(lease, *args, **kwargs)
        except BaseException as error:
            record("pulse_error", error)
            raise
        else:
            record("pulse_return")  # May be throttled: only *_db_commit proves commit.
            return result
        finally:
            role.reset(token)

    def recovery(bound_factory, *args, **kwargs):
        if not any(bound_factory is item for item in factories):
            return original_recovery(bound_factory, *args, **kwargs)
        token = role.set("recovery")
        record("recovery_enter")
        try:
            result = original_recovery(bound_factory, *args, **kwargs)
        except BaseException as error:
            record("recovery_error", error)
            raise
        else:
            record("recovery_return")
            return result
        finally:
            role.reset(token)

    def serve(*args, **kwargs):
        try:
            return original_serve(*args, **kwargs)
        except BaseException as error:
            record("serve_one_error", error)
            raise

    from rd_owner_lock_diagnostic import observe_owner_transactions

    def matches(session):
        return any(session.get_bind() is item.kw["bind"] for item in factories)

    with ExitStack() as stack:
        stack.enter_context(observe_owner_transactions(record, matches))
        event.listen(Session, "after_commit", committed)
        stack.callback(event.remove, Session, "after_commit", committed)
        stack.enter_context(patch.object(rdagent_queue, "claim_configured_run", claim))
        stack.enter_context(patch.object(AttemptLease, "pulse", pulse))
        stack.enter_context(patch.object(operational, "_recover_and_record", recovery))
        stack.enter_context(patch.object(operational, "serve_one", serve))
        try:
            yield
        finally:
            try:
                if lock.acquire(blocking=False):
                    try:
                        report = {
                            "status": "best_effort_nonblocking",
                            "limit": maximum,
                            "at_capacity": len(events) == maximum,
                            "dropped_oldest": dropped,
                            "events": list(events),
                        }
                    finally:
                        lock.release()
                    print(
                        "RD_TIMELINE_V1:" + json.dumps(report, sort_keys=True),
                        flush=True,
                    )
            except BaseException:  # noqa: BLE001, S110 -- retain worker error
                pass
