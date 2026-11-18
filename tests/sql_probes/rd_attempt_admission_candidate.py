"""Test-only local admission candidate, NOT execution or lease authority.

The caller supplies an existing lease expiry and clocks sampled BEFORE reading
it. This produces one conservative scheduling cutoff, never a renewed lease or
controller deadline. No database access, pulse, runtime patch or retry occurs.

An adopter must enclose its ENTIRE original transaction, including deferred
checks and commit, then retain all original deadline and live-fence validation.
This helper alone does not prove that runtime callbacks use the same gate.
Contexts cannot transfer threads. A wrong-thread exit rejects and permanently
closes local admission; it does not move a database transaction to that thread.
"""

import math
import threading
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from time import monotonic

from packages.research_runtime.fences import AttemptFence


class AdmissionRejected(RuntimeError):
    """Only a fixed reason is exposed, never factory or fence contents."""


class AttemptAdmission:
    def __init__(
        self,
        factory,
        fence,
        *,
        lease_expires_at,
        observation_started_utc,
        observation_started_monotonic,
        clock=monotonic,
    ):
        if factory is None or type(fence) is not AttemptFence:
            raise AdmissionRejected("invalid_binding")
        for value in (lease_expires_at, observation_started_utc):
            if type(value) is not datetime or value.utcoffset() is None:
                raise AdmissionRejected("invalid_expiry_observation")
        if (
            type(observation_started_monotonic) not in (int, float)
            or not math.isfinite(observation_started_monotonic)
            or not callable(clock)
        ):
            raise AdmissionRejected("invalid_clock")
        cutoff = (
            observation_started_monotonic
            + (lease_expires_at - observation_started_utc).total_seconds()
        )
        if not math.isfinite(cutoff):
            raise AdmissionRejected("invalid_clock")
        self._factory, self._fence = factory, fence
        self._cutoff, self._clock = cutoff, clock
        self._last_clock = observation_started_monotonic
        self._condition = threading.Condition(threading.Lock())
        self._queue = deque()
        self._participants = {}
        self._owner = None
        self._closed = False

    def _remaining(self):
        try:
            now = self._clock()
        except Exception:  # noqa: BLE001 -- reject opaque clock errors without details
            raise AdmissionRejected("clock_unavailable") from None
        if (
            type(now) not in (int, float)
            or not math.isfinite(now)
            or now < self._last_clock
        ):
            raise AdmissionRejected("invalid_clock")
        self._last_clock = now
        remaining = self._cutoff - now
        if not math.isfinite(remaining) or remaining <= 0:
            raise AdmissionRejected("expired")
        return remaining

    @contextmanager
    def transaction(self, factory, fence, *, role):
        """FIFO local admission; roles describe callers, never grant authority."""
        if (
            factory is not self._factory
            or type(fence) is not AttemptFence
            or fence != self._fence
        ):
            raise AdmissionRejected("binding_mismatch")
        if type(role) is not str or role not in {"neutral", "heartbeat"}:
            raise AdmissionRejected("invalid_role")
        thread = threading.get_ident()
        ticket = object()
        try:
            with self._condition:
                if thread in self._participants:
                    raise AdmissionRejected("nested_admission")
                if self._closed:
                    raise AdmissionRejected("closed")
                self._participants[thread] = ticket
                self._queue.append(ticket)
                while True:
                    if self._closed:
                        raise AdmissionRejected("closed")
                    remaining = self._remaining()
                    if self._owner is None and self._queue[0] is ticket:
                        self._queue.popleft()
                        self._owner = ticket
                        break
                    self._condition.wait(timeout=min(remaining, threading.TIMEOUT_MAX))
            # Expiry is checked after every wait before admission. Existing
            # transaction code must still check its live fence and deadlines.
            yield
        finally:
            with self._condition:
                # Identity also protects an outer holder from nested rejection.
                if self._participants.get(thread) is ticket:
                    wrong_thread = threading.get_ident() != thread
                    if wrong_thread:
                        self._closed = True
                    if self._owner is ticket:
                        self._owner = None
                    if ticket in self._queue:
                        self._queue.remove(ticket)
                    del self._participants[thread]
                    self._condition.notify_all()
                    if wrong_thread:
                        raise AdmissionRejected("thread_mismatch")

    def close(self):
        """Reject pending/future admissions; never interrupt an active body."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
