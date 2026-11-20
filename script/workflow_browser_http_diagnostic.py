"""Test-server-only bounded HTTP observations; never retain URLs or payloads."""

import re
import threading
import time
from collections import deque

_ROUTE = re.compile(
    r"/api/v1/engine-runs/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"(?:/(history|inputs))?"
)
MAX_OBSERVATIONS = 64
MAX_DURATION_MS = 180_000


class WorkflowHTTPDiagnostic:
    """Pass through ASGI unchanged and project only fixed route/status/timing fields.

    Snapshot runs from the harness thread while requests run in uvicorn's thread.
    The last 64 starts include unfinished requests, without unbounded active state.
    These are server observations, not proof of browser receipt or rendering.
    """

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._observations = deque(maxlen=MAX_OBSERVATIONS)

    async def __call__(self, scope, receive, send):
        match = (
            _ROUTE.fullmatch(scope.get("path", ""))
            if scope["type"] == "http" and scope.get("method") == "GET"
            else None
        )
        if match is None:
            return await self.app(scope, receive, send)
        entry = {
            "route": match[1] or "detail",
            "status": "pending",
            "outcome": "pending",
            "started": time.monotonic(),
            "finished": None,
        }
        with self._lock:
            self._observations.append(entry)

        async def observed_send(message):
            if message["type"] == "http.response.start":
                status = message.get("status")
                with self._lock:
                    entry["status"] = (
                        status
                        if type(status) is int and 100 <= status <= 599
                        else "other"
                    )
            await send(message)

        outcome = "error"
        try:
            await self.app(scope, receive, observed_send)
            outcome = "complete"
        finally:
            with self._lock:
                entry["outcome"] = outcome
                entry["finished"] = time.monotonic()

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            return [
                {
                    "route": entry["route"],
                    "status": entry["status"],
                    "outcome": entry["outcome"],
                    "duration_ms": min(
                        MAX_DURATION_MS,
                        max(
                            0,
                            int(
                                (
                                    (
                                        entry["finished"]
                                        if entry["finished"] is not None
                                        else now
                                    )
                                    - entry["started"]
                                )
                                * 1000
                            ),
                        ),
                    ),
                }
                for entry in self._observations
            ]
