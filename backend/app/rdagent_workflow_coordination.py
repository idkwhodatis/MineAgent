"""Process-local scheduling for one committed v3 RD worker attempt.

This gate is not authority. PostgreSQL locks, peer validation and the live
attempt fence still decide admission. Never include RPC or process I/O here.
"""

from collections import deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from threading import Condition, Lock, get_ident
from time import monotonic, monotonic_ns

from packages.rdagent_provider.process_receipts import SDKStopped
from packages.research_runtime.fences import AttemptFence

OWNER_WAIT_SECONDS = 5.0

_entries = {}
_registry_lock = Lock()
_controller = ContextVar("rd_workflow_controller_deadline", default=None)
_sdk_stop = ContextVar("rd_workflow_sdk_stop_admission", default=None)


def _rejected(heartbeat):
    from backend.app.services import DomainError, FenceLost

    if heartbeat:
        return FenceLost()
    return DomainError(
        "neutral_busy", "Neutral execution authority is unavailable", 409
    )


class _Gate:
    def __init__(self):
        self._condition = Condition()
        self._owner = None
        self._waiters = deque()
        self._closed = False

    @contextmanager
    def hold(self, deadline, *, heartbeat=False):
        ident = get_ident()
        ticket = object()
        with self._condition:
            if self._closed or self._owner == ident:
                # A different Session on this thread must never wait on its
                # own transaction (including in Session begin/commit hooks).
                raise _rejected(heartbeat)
            self._waiters.append(ticket)
            try:
                while True:
                    remaining = deadline - monotonic()
                    if self._closed or remaining <= 0:
                        raise _rejected(heartbeat)
                    if self._owner is None and self._waiters[0] is ticket:
                        self._owner = ident
                        break
                    self._condition.wait(remaining)
            finally:
                self._waiters.remove(ticket)
                self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._owner = None
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def _lookup(factory, fence):
    if type(fence) is not AttemptFence:
        return None
    with _registry_lock:
        entry = _entries.get(id(factory))
        return (
            entry[2]
            if entry is not None and entry[0] is factory and entry[1] == fence
            else None
        )


@contextmanager
def registered(factory, fence):
    """Bind only the queue's existing trusted, committed v3 claim."""
    if type(fence) is not AttemptFence:
        raise _rejected(heartbeat=True)
    entry = (factory, fence, _Gate())
    key = id(factory)
    with _registry_lock:
        if key in _entries:
            raise _rejected(heartbeat=True)
        _entries[key] = entry
    try:
        yield entry[2]
    finally:
        with _registry_lock:
            entry[2].close()
            del _entries[key]


@contextmanager
def controller_deadline(factory, fence, deadline):
    """Use the existing RPC deadline only while serving its request."""
    gate = _lookup(factory, fence)
    if gate is None:
        yield
        return
    previous = _controller.get()
    if previous is not None and previous[0] is gate:
        deadline = min(deadline, previous[1])
    token = _controller.set((gate, deadline))
    try:
        yield
    finally:
        _controller.reset(token)


@contextmanager
def _positive_sdk_stop_admission(factory, fence, receipt):
    """Only admit retention of an already observed, positively stopped SDK.

    Do not change the controller clock, database fence, or PG NOWAIT. All
    receipt transactions and conservative unknown settlement use the ordinary
    controller cutoff when it is later. Otherwise they share one cutoff set
    by the stop observation, even across separate scopes. The caller must
    leave this scope before processing any response or success.
    """
    if (
        type(receipt) is not SDKStopped
        or receipt.leader_reaped is not True
        or receipt.process_group_absent is not True
        or any(
            getattr(receipt, key) != getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        )
    ):
        raise _rejected(heartbeat=False)
    gate = _lookup(factory, fence)
    current = _controller.get()
    if gate is None or current is None or current[0] is not gate:
        yield
        return
    if _sdk_stop.get() is not None:
        raise _rejected(heartbeat=False)
    observed = receipt.observed_monotonic_ns
    cutoff_ns = observed + int(OWNER_WAIT_SECONDS * 1_000_000_000)
    now = monotonic_ns()
    if observed > now:
        raise _rejected(heartbeat=False)
    cutoff = cutoff_ns / 1_000_000_000
    if current[1] >= cutoff:
        # Never shorten normal receipt work or its per-transaction wait.
        yield
        return
    if now >= cutoff_ns:
        raise _rejected(heartbeat=False)
    token = _sdk_stop.set((gate, get_ident(), cutoff))
    try:
        yield
    finally:
        _sdk_stop.reset(token)


def neutral(factory, fence):
    gate = _lookup(factory, fence)
    if gate is None:
        return nullcontext()
    deadline = monotonic() + OWNER_WAIT_SECONDS
    current = _controller.get()
    if current is not None and current[0] is gate:
        stop = _sdk_stop.get()
        if stop is not None and stop[0] is gate and stop[1] == get_ident():
            deadline = min(deadline, stop[2])
        else:
            deadline = min(deadline, current[1])
    return gate.hold(deadline)


def heartbeat(factory, fence, *, deadline):
    gate = _lookup(factory, fence)
    return nullcontext() if gate is None else gate.hold(deadline, heartbeat=True)
