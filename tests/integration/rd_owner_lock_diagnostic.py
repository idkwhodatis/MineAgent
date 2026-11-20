"""Observe successful Python owner-lock acquisitions; never query or acquire locks.

This is not a PostgreSQL lock inventory: SQL-internal acquisitions and unrelated
processes are invisible. Ordinals are scope-local, not database identities.
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch
from weakref import WeakKeyDictionary


@contextmanager
def observe_owner_transactions(record, matches):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from backend.app import rdagent_controller_recovery as controller
    from backend.app import research_lifecycle as lifecycle
    from backend.app import services
    from workers.research import analysis_process, numeric_process

    role = ContextVar("rd_owner_role", default=None)
    tracked = WeakKeyDictionary()
    mutex = Lock()
    ordinal = 0

    def emit(name, label, number):
        try:
            record(name, owner_role=label, transaction_ordinal=number)
        except BaseException:  # noqa: BLE001, S110 -- observer cannot alter execution
            pass

    def acquired(session, scope):
        nonlocal ordinal
        try:
            label = role.get()
            if (
                label is None
                or type(scope) is not str
                or not scope.startswith("submit:")
                or not matches(session)
                or session.in_nested_transaction()
            ):
                return
            transaction = session.get_transaction()
            if transaction is None or not mutex.acquire(blocking=False):
                return
            try:
                if session in tracked or len(tracked) >= 1024:
                    return
                ordinal = min(1_000_000, ordinal + 1)
                tracked[session] = (transaction, label, ordinal)
                number = ordinal
            finally:
                mutex.release()
            emit("owner_lock_acquired", label, number)
        except BaseException:  # noqa: BLE001, S110
            pass

    def finish(session, outcome, transaction=None):
        try:
            if session.in_nested_transaction() or not mutex.acquire(blocking=False):
                return
            try:
                value = tracked.get(session)
                if value is None or (
                    transaction is not None and transaction is not value[0]
                ):
                    return
                del tracked[session]
            finally:
                mutex.release()
            emit("owner_transaction_" + outcome, value[1], value[2])
        except BaseException:  # noqa: BLE001, S110
            pass

    def committed(session):
        finish(session, "commit")

    def rolled_back(session):
        finish(session, "rollback")

    def ended(session, transaction):
        if transaction.parent is None:
            finish(session, "end_unclassified", transaction)

    original_neutral = lifecycle._neutral_transaction_once

    @contextmanager
    def neutral(factory, fence, *, resource_scope="none"):
        scopes = {"none", "objects", "processes", "factor_objects", "factor_numeric"}
        label = (
            "neutral_" + resource_scope
            if type(resource_scope) is str and resource_scope in scopes
            else None
        )
        token = role.set(label)
        try:
            with original_neutral(
                factory, fence, resource_scope=resource_scope
            ) as session:
                yield session
        finally:
            role.reset(token)

    def lock_wrapper(original):
        def observe(session, scope):
            result = original(session, scope)
            acquired(session, scope)
            return result

        return observe

    def containment(original, label):
        def observe(*args, **kwargs):
            token = role.set(label)
            try:
                return original(*args, **kwargs)
            finally:
                role.reset(token)

        return observe

    with ExitStack() as stack:
        for name, callback in (
            ("after_commit", committed),
            ("after_rollback", rolled_back),
            ("after_transaction_end", ended),
        ):
            event.listen(Session, name, callback)
            stack.callback(event.remove, Session, name, callback)
        stack.enter_context(
            patch.object(lifecycle, "_neutral_transaction_once", neutral)
        )
        stack.enter_context(
            patch.object(
                lifecycle,
                "_try_neutral_lock",
                lock_wrapper(lifecycle._try_neutral_lock),
            )
        )
        stack.enter_context(
            patch.object(
                services, "advisory_lock", lock_wrapper(services.advisory_lock)
            )
        )
        # Controller imports its lock alias eagerly; numeric/analysis import lazily.
        stack.enter_context(
            patch.object(
                controller, "advisory_lock", lock_wrapper(controller.advisory_lock)
            )
        )
        for module, name, label in (
            (numeric_process, "record_numeric_containment", "numeric_containment"),
            (analysis_process, "record_analysis_containment", "analysis_containment"),
            (controller, "record_controller_containment", "controller_containment"),
        ):
            stack.enter_context(
                patch.object(module, name, containment(getattr(module, name), label))
            )
        yield
