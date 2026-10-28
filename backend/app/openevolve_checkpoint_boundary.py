"""Host-local stable epoch projection; not database or peer authority.

Ordinary handlers remain concurrent. A checkpoint publication drains admitted
handlers and blocks new ones until its complete retained graph has committed.
External writes are still rejected by the original exact inventory validator.
"""

import math
import threading
import time
from contextlib import contextmanager


class CheckpointBoundary:
    def __init__(self):
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting = 0
        self._closed = False

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _remaining(self, deadline):
        if self._closed:
            raise ValueError("Checkpoint boundary disconnected")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Original checkpoint boundary deadline expired")
        return remaining

    @contextmanager
    def enter(self, *, checkpoint, deadline):
        if (
            type(checkpoint) is not bool
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
        ):
            raise ValueError("Exact checkpoint boundary admission required")
        with self._condition:
            if checkpoint:
                self._waiting += 1
            try:
                while True:
                    remaining = self._remaining(deadline)
                    busy = self._writer or (
                        self._readers if checkpoint else self._waiting
                    )
                    if not busy:
                        break
                    self._condition.wait(min(0.05, remaining))
                if checkpoint:
                    self._writer = True
                else:
                    self._readers += 1
            finally:
                if checkpoint:
                    self._waiting -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                if checkpoint:
                    self._writer = False
                else:
                    self._readers -= 1
                self._condition.notify_all()
