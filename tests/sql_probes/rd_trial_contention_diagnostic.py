"""Bounded test-only chronology; no lockholder/causality or authority claim.

Containment categories remain active until the existing root transaction ends.
Sampling is nonblocking and may omit observations. No SQL, waits, owner IDs,
fence values, exception messages, or new transaction are introduced.
"""

import math
from contextlib import contextmanager
from threading import Lock
from time import monotonic
from weakref import WeakKeyDictionary, ref

from sqlalchemy import event
from sqlalchemy.orm import Session

CATEGORIES = ("numeric", "analysis", "controller")
MAX_EVENTS = 128
MAX_MS = 3_600_000


class ContentionDiagnostic:
    def __init__(self):
        self._lock = Lock()
        self._sessions = WeakKeyDictionary()
        self._held = WeakKeyDictionary()
        self._roots = WeakKeyDictionary()
        self._uncertain = False
        self._events = []
        self._overflow = False
        try:
            self._start = monotonic()
        except BaseException:  # noqa: BLE001 -- optional diagnostic clock
            self._start = None

    def _current_root(self, session):
        root_ref = self._roots.get(session)
        root = root_ref() if root_ref is not None else None
        return root is not None and root.is_active and session.get_transaction() is root

    def _record(self, kind, category="none"):
        if self._start is None:
            return
        elapsed = monotonic() - self._start
        if not math.isfinite(elapsed) or elapsed < 0:
            return
        active = {
            name: min(
                1000,
                sum(
                    value == name and self._current_root(session)
                    for session, value in self._sessions.items()
                ),
            )
            for name in CATEGORIES
        }
        if len(self._events) == MAX_EVENTS:
            self._overflow = True
            return
        self._events.append(
            {
                "kind": kind,
                "category": category,
                "elapsed_ms": min(MAX_MS, int(elapsed * 1000)),
                "active": active,
                "owner_held": {
                    name: min(
                        1000,
                        sum(
                            value == name and self._current_root(session)
                            for session, value in self._held.items()
                        ),
                    )
                    for name in CATEGORIES
                },
            }
        )

    def _observe(self, action):
        acquired = False
        try:
            acquired = self._lock.acquire(blocking=False)
            if acquired:
                action()
            else:
                self._uncertain = True
        except BaseException:  # noqa: BLE001 -- observation cannot alter native execution
            self._uncertain = True
        finally:
            if acquired:
                try:
                    self._lock.release()
                except BaseException:  # noqa: BLE001, S110 -- diagnostic lock only
                    pass

    def begin(self, session, category):
        def action():
            if category not in CATEGORIES:
                return
            if session in self._sessions and self._current_root(session):
                return
            self._held.pop(session, None)
            self._roots.pop(session, None)
            root = session.get_transaction()
            if root is not None:
                self._roots[session] = ref(root)
            self._sessions[session] = category
            self._record("containment_begin", category)

        self._observe(action)

    def end(self, session):
        def action():
            category = self._sessions.pop(session, None)
            self._held.pop(session, None)
            self._roots.pop(session, None)
            if category is not None:
                self._record("containment_end", category)

        self._observe(action)

    def owner_acquired(self, session):
        def action():
            category = self._sessions.get(session)
            root = session.get_transaction()
            nested = session.get_nested_transaction()
            if nested is not None and nested.is_active:
                self._uncertain = True
                return
            existing = self._roots.get(session)
            if existing is not None and existing() is not root:
                return
            if category is not None and root is not None and root.is_active:
                self._roots[session] = ref(root)
                self._held[session] = category
                self._record("owner_acquired", category)

        self._observe(action)

    def wrap_owner_lock(self, original):
        def observed(session, scope):
            result = original(session, scope)
            try:
                if type(scope) is str and scope.startswith("submit:"):
                    self.owner_acquired(session)
            except BaseException:  # noqa: BLE001, S110 -- only observe after original acquisition
                pass
            return result

        return observed

    def pulse_failure(self):
        self._observe(lambda: self._record("pulse_failure"))

    def body_failure(self):
        self._observe(lambda: self._record("trial_body_failure"))

    def wrap(self, original, category):
        def observed(session, *args, **kwargs):
            self.begin(session, category)
            try:
                return original(session, *args, **kwargs)
            finally:
                try:
                    if not session.in_transaction():
                        self.end(session)
                except BaseException:  # noqa: BLE001, S110 -- metadata check cannot affect original result
                    pass

        return observed

    def transaction_end(self, session, transaction):
        try:
            if transaction.parent is None:
                self.end(session)
        except BaseException:  # noqa: BLE001, S110 -- original commit/rollback always wins
            pass

    def report(self):
        try:
            print(
                "RD_TRIAL_CONTENTION_V1",
                {
                    "status": "best_effort_nonblocking",
                    "overflow": self._overflow,
                    "uncertain": self._uncertain,
                    "events": self._events,
                },
            )
        except BaseException:  # noqa: BLE001, S110 -- best-effort output
            pass


@contextmanager
def observe_contention():
    diagnostic = ContentionDiagnostic()
    patches = []
    listening = False
    try:
        try:
            from backend.app import (
                analysis_launch_services,
                numeric_launch_services,
                rdagent_controller_recovery,
                services,
            )
            from workers.research import analysis_process, numeric_process

            targets = (
                (numeric_launch_services, "record_numeric_containment", "numeric"),
                (numeric_process, "record_numeric_containment", "numeric"),
                (analysis_launch_services, "record_analysis_containment", "analysis"),
                (analysis_process, "record_analysis_containment", "analysis"),
                (
                    rdagent_controller_recovery,
                    "record_controller_containment",
                    "controller",
                ),
            )
            event.listen(Session, "after_transaction_end", diagnostic.transaction_end)
            listening = True
            original_lock = services.advisory_lock
            services.advisory_lock = diagnostic.wrap_owner_lock(original_lock)
            patches.append((services, "advisory_lock", original_lock))
            controller_lock = rdagent_controller_recovery.advisory_lock
            rdagent_controller_recovery.advisory_lock = diagnostic.wrap_owner_lock(
                controller_lock
            )
            patches.append(
                (rdagent_controller_recovery, "advisory_lock", controller_lock)
            )
            for module, name, category in targets:
                original = getattr(module, name)
                setattr(module, name, diagnostic.wrap(original, category))
                patches.append((module, name, original))
        except BaseException:  # noqa: BLE001 -- instrumentation setup is optional
            diagnostic._uncertain = True
        yield diagnostic
    finally:
        for module, name, original in reversed(patches):
            try:
                setattr(module, name, original)
            except BaseException:  # noqa: BLE001, S110 -- preserve original failure
                pass
        if listening:
            try:
                event.remove(
                    Session, "after_transaction_end", diagnostic.transaction_end
                )
            except BaseException:  # noqa: BLE001, S110 -- preserve original failure
                pass
        diagnostic.report()
