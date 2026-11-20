"""Closed HTTP projection only; no actual workflow browser acceptance claim."""

import asyncio
import json
from pathlib import Path

import pytest

from script import workflow_browser_http_diagnostic as diagnostic

PATH = "/api/v1/engine-runs/12345678-1234-1234-1234-123456789abc"
SECRET = "private-payload-token-path"


def test_allowlist_and_transparent_messages():
    async def scenario():
        calls, sent = [], []
        messages = [
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [(b"secret", SECRET.encode())],
            },
            {"type": "http.response.body", "body": SECRET.encode()},
        ]

        async def receive():
            return {"type": "http.request", "body": SECRET.encode()}

        async def send(message):
            sent.append(message)

        async def app(scope, incoming, outgoing):
            calls.append((scope, incoming))
            for message in messages:
                await outgoing(message)

        wrapped = diagnostic.WorkflowHTTPDiagnostic(app)
        for suffix in ("", "/history", "/inputs"):
            scope = {
                "type": "http",
                "method": "GET",
                "path": PATH + suffix,
                "query_string": SECRET.encode(),
                "headers": [(b"authorization", SECRET.encode())],
            }
            await wrapped(scope, receive, send)
            assert calls[-1] == (scope, receive)
            assert sent[-2:] == messages
            assert sent[-1] is messages[-1]
        snapshots = wrapped.snapshot()
        assert [entry["route"] for entry in snapshots] == [
            "detail",
            "history",
            "inputs",
        ]
        for entry in snapshots:
            assert set(entry) == {"route", "status", "outcome", "duration_ms"}
            assert entry["status"] == 503 and entry["outcome"] == "complete"
            assert 0 <= entry["duration_ms"] <= 180_000
        encoded = json.dumps(snapshots)
        assert SECRET not in encoded and "12345678" not in encoded
        for kind, method, path in [
            ("http", "POST", PATH),
            ("websocket", "GET", PATH),
            ("http", "GET", PATH + "/secret"),
            ("http", "GET", "/api/v1/auth/login"),
            ("http", "GET", "/api/v1/engine-runs/" + SECRET),
        ]:
            await wrapped({"type": kind, "method": method, "path": path}, receive, send)
        assert wrapped.snapshot() == snapshots

    asyncio.run(scenario())


def test_bounded_pending_duration_and_failure(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: now[0])

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            started.set()
            await release.wait()
            raise RuntimeError(SECRET)

        wrapped = diagnostic.WorkflowHTTPDiagnostic(app)
        task = asyncio.create_task(
            wrapped({"type": "http", "method": "GET", "path": PATH}, None, None)
        )
        await started.wait()
        now[0] = 999.0
        assert wrapped.snapshot() == [
            {
                "route": "detail",
                "status": "pending",
                "outcome": "pending",
                "duration_ms": 180_000,
            }
        ]
        release.set()
        with pytest.raises(RuntimeError, match=SECRET):
            await task
        assert wrapped.snapshot()[0]["outcome"] == "error"
        for _ in range(100):
            with pytest.raises(RuntimeError):
                await wrapped(
                    {"type": "http", "method": "GET", "path": PATH + "/inputs"},
                    None,
                    None,
                )
        assert len(wrapped.snapshot()) == 64
        assert all(entry["route"] == "inputs" for entry in wrapped.snapshot())
        assert SECRET not in json.dumps(wrapped.snapshot())

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [SECRET, True, 0, 600, None])
def test_unknown_status_cannot_escape(status):
    async def scenario():
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": status})

        async def send(message):
            pass

        wrapped = diagnostic.WorkflowHTTPDiagnostic(app)
        await wrapped({"type": "http", "method": "GET", "path": PATH}, None, send)
        assert wrapped.snapshot()[0]["status"] == "other"

    asyncio.run(scenario())


def test_harness_only_attaches_snapshot_on_failure():
    source = Path("tests/integration/workflow_history_browser.py").read_text()
    assert (
        'uvicorn.Config(http_diagnostic, log_level="error", access_log=False)' in source
    )
    assert "except OwnedCommandFailed:" in source
    assert 'diagnostic["http_observations"] = http_diagnostic.snapshot()' in source
    assert "seconds=120" in source
