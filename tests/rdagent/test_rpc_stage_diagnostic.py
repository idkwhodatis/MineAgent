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
