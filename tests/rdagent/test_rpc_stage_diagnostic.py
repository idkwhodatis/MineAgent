"""Actual RPC codec with explicit channel/clock doubles; no Docker timing claim."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from runtimes.rdagent_operational import rpc
from workers.research import rdagent_operational as worker


def _diagnostic():
    path = Path(__file__).parents[1] / "integration/rd_rpc_diagnostic.py"
    spec = importlib.util.spec_from_file_location("rd_rpc_diagnostic_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Channel:
    def __init__(self):
        self.raw = rpc.encode(
            {
                "schema_version": rpc.SCHEMA,
                "sequence": 0,
                "ticket": "secret-ticket",
                "method": "execute_or_reuse",
                "body": {"secret": "private-payload"},
            }
        )
        self.sent = []

    def settimeout(self, value):
        assert value > 0

    def recv(self, count):
        chunk, self.raw = self.raw[:count], self.raw[count:]
        return chunk

    def sendall(self, raw):
        self.sent.append(raw)


def test_actual_rpc_timing_preserves_deadline_and_redacts_payload(monkeypatch, capsys):
    module = _diagnostic()
    now = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    channel = Channel()
    observed = []
    original = worker.serve_one

    def handler(authority, method, body):
        observed.append((authority, method, body))
        now[0] = 60.001
        return {"private-result": "secret"}

    with pytest.raises(rpc.RPCError, match="^deadline$"), module.diagnose_rd_rpc():
        worker.serve_one(
            channel,
            sequence=0,
            deadline=60,
            authenticate=lambda *_: "actual-authority",
            handle=handler,
        )
    assert len(observed) == 1 and not channel.sent
    assert worker.serve_one is original
    output = capsys.readouterr().out
    assert all(v not in output for v in ("secret", "private", "actual-authority"))
    data = json.loads(output.removeprefix("RD_RPC_TIMING:"))
    assert [item["stage"] for item in data["tail"]] == [
        "read",
        "auth",
        "handle",
        "send",
    ]
    assert data["tail"][-1]["remaining_ms"] == -1
    assert data["tail"][-1]["ok"] is False


def test_actual_rpc_timing_observes_real_session_commit_without_changing_reply(
    monkeypatch, capsys
):
    module = _diagnostic()
    now = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    channel = Channel()
    engine = create_engine("sqlite://")

    def handler(*_):
        with Session(engine) as session, session.begin():
            now[0] += 0.2
        return {"ok": True}

    try:
        with module.diagnose_rd_rpc():
            assert (
                worker.serve_one(
                    channel,
                    sequence=0,
                    deadline=60,
                    authenticate=lambda *_: None,
                    handle=handler,
                )
                == 1
            )
    finally:
        engine.dispose()
    assert len(channel.sent) == 1
    data = json.loads(capsys.readouterr().out.removeprefix("RD_RPC_TIMING:"))
    assert [item["stage"] for item in data["tail"]] == [
        "read",
        "auth",
        "commit",
        "handle",
        "send",
    ]
    assert all(item["ok"] for item in data["tail"])


def test_queue_diagnostic_preserves_exception_and_redacts_unknown_codes(
    monkeypatch, capsys
):
    from packages.research_runtime.process_contracts import ProcessError
    from workers.research import rdagent_queue

    module = _diagnostic()
    error = ProcessError("secret-source-url-credential")
    seen = []

    def execute(*args, **kwargs):
        seen.append((args, kwargs))
        raise error

    monkeypatch.setattr(rdagent_queue, "execute", execute)
    monkeypatch.setattr(module, "_queue_state", lambda *a: {"status": "failed"})
    with (
        pytest.raises(ProcessError) as raised,
        module.diagnose_rd_queue(object(), "private-run-id"),
    ):
        rdagent_queue.execute("private-run-id", 1, settings="private-settings")
    assert raised.value is error and rdagent_queue.execute is execute
    assert len(seen) == 1
    output = capsys.readouterr().out
    assert not any(s in output for s in ("secret", "private", "credential", "url"))
    lines = output.splitlines()
    assert json.loads(lines[0].removeprefix("RD_QUEUE_FAILURE:")) == {
        "family": "process",
        "code": "unknown",
        "stage": "unknown",
        "sqlstate": "none",
    }
    assert lines[1].startswith("RD_QUEUE_STATE:")
    assert module._queue_failure(rpc.RPCError("deadline"))["code"] == "deadline"
    assert module._queue_failure(rpc.RPCError("secret"))["code"] == "unknown"

    def unavailable(*args):
        raise ValueError("secret-database-url")

    monkeypatch.setattr(module, "_queue_state", unavailable)
    with (
        pytest.raises(ProcessError) as repeated,
        module.diagnose_rd_queue(object(), "private-run-id"),
    ):
        rdagent_queue.execute("private-run-id", 1, settings="private-settings")
    assert repeated.value is error and rdagent_queue.execute is execute
    output = capsys.readouterr().out
    assert "diagnostic_unavailable" in output and "secret" not in output


def test_queue_state_is_bounded_and_only_emits_allowlisted_fields():
    from unittest.mock import MagicMock
    from uuid import uuid4

    module = _diagnostic()
    session = MagicMock()
    session.get.return_value = SimpleNamespace(status="secret-status")
    session.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="failed",
            error="secret-message",
            sandbox_stopped=True,
            exit_code=True,
            worker_id="secret-worker",
            sandbox_id="secret-container",
        )
    ] * 9
    session.execute.return_value.all.return_value = [
        ("secret-role", "secret-state")
    ] * 17
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    state = module._queue_state(factory, uuid4())
    assert state["attempt_overflow"] and len(state["attempts"]) == 8
    assert state["unit_overflow"] and len(state["units"]) == 16
    assert state["status"] == "unknown"
    assert state["attempts"][0]["exit_code"] is None
    assert state["attempts"][0]["error"] == "other"
    assert "secret" not in json.dumps(state)
