"""Test-only FIFO transaction scheduling, not execution/fencing authority.

Observation finishes and its session closes before enqueue. Each ticket retains
its own immutable cutoff; no waiting ticket can acquire a renewal. The caller
must enclose original SQL through deferred checks and commit, never external I/O.
No pulse wrapper or native/runtime patch is installed here.
"""

import threading
from collections import deque
from contextlib import contextmanager
from time import monotonic

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes.rd_admission_expiry_observation import CommittedExpiryObserver
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected


class TicketedAdmission:
    def __init__(self, factory, fence, *, clock=monotonic):
        self._observer = CommittedExpiryObserver(factory, fence)
        self._factory, self._fence, self._clock = factory, fence, clock
        self._condition = threading.Condition(threading.Lock())
        self._participants = {}
        self._queue = deque()
        self._owner = None
        self._closed = False

    @contextmanager
    def transaction(self, factory, fence, *, role):
        if (
            factory is not self._factory
            or type(fence) is not AttemptFence
            or fence != self._fence
        ):
            raise AdmissionRejected("binding_mismatch")
        if type(role) is not str or role not in {"neutral", "heartbeat"}:
            raise AdmissionRejected("invalid_role")
        thread, identity = threading.get_ident(), object()
        try:
            # Register before observation, so reentrant observation cannot nest.
            with self._condition:
                if thread in self._participants:
                    raise AdmissionRejected("nested_admission")
                if self._closed:
                    raise AdmissionRejected("closed")
                self._participants[thread] = identity
            ticket = self._observer.observe(
                factory, fence, role=role, clock=self._clock
            )
            # FIFO order is completed-observation enqueue order, not call time.
            # Observation owns no gate and its read transaction has closed here.
            with self._condition:
                self._queue.append(identity)
                while True:
                    if self._closed:
                        raise AdmissionRejected("closed")
                    remaining = ticket.remaining(
                        factory, fence, role=role, clock=self._clock
                    )
                    if self._owner is None and self._queue[0] is identity:
                        self._queue.popleft()
                        self._owner = identity
                        break
                    self._condition.wait(timeout=min(remaining, threading.TIMEOUT_MAX))
            yield ticket
        finally:
            with self._condition:
                if self._participants.get(thread) is identity:
                    wrong_thread = threading.get_ident() != thread
                    if wrong_thread:
                        self._closed = True
                    if self._owner is identity:
                        self._owner = None
                    if identity in self._queue:
                        self._queue.remove(identity)
                    del self._participants[thread]
                    self._condition.notify_all()
                    if wrong_thread:
                        raise AdmissionRejected("thread_mismatch")

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
