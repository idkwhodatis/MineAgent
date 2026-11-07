"""Bounded, atomic per-process login backstop; a shared proxy limit is still required."""

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    inflight: int = 0


class LoginLimiter:
    def __init__(self, *, attempts=10, window_seconds=60, max_hosts=10_000):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.max_hosts = max_hosts
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def _expire(self, bucket, now):
        while bucket.failures and bucket.failures[0] <= now - self.window_seconds:
            bucket.failures.popleft()

    def begin(self, host):
        """Reserve before password work so concurrent failures cannot bypass the cap."""
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(host)
            if bucket is None:
                if len(self._buckets) >= self.max_hosts:
                    for key, existing in list(self._buckets.items()):
                        self._expire(existing, now)
                        if not existing.failures and not existing.inflight:
                            del self._buckets[key]
                    # Never erase a live host's rate limit to admit another host.
                    if len(self._buckets) >= self.max_hosts:
                        return None
                bucket = self._buckets[host] = _Bucket()
            self._expire(bucket, now)
            if len(bucket.failures) + bucket.inflight >= self.attempts:
                return None
            bucket.inflight += 1
            return bucket

    def finish(self, bucket, *, successful):
        with self._lock:
            bucket.inflight -= 1
            if not successful:
                bucket.failures.append(time.monotonic())
