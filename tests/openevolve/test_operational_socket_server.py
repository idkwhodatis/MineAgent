"""Real Unix socketpair framing with explicit peer/domain doubles, not Docker/PG."""

import hashlib
import socket
import struct
import threading
import time
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_socket_server as server
from backend.app.openevolve_broker_rpc import FixtureSchedule
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.research_runtime.engine_broker_contracts import OEChatResponse
from runtimes.openevolve_operational.wire import parse_event, reply


def initial():
    epoch = uuid4()
    running = NS(
        child=object(),
        connection=object(),
        finished=False,
        deadline=time.monotonic() + 2,
        launch=NS(intent=NS(admission=object())),
        observer_context=object(),
    )
    return server.InitialEpochServer(
        None,
        object(),
        running,
        object(),
        epoch_id=epoch,
        final_frontier=1,
        ticket_key=b"k" * 32,
        schedule=FixtureSchedule((OEChatResponse(content="fixture"),)),
        settings=None,
    )


def wire(unit, kind="checkpoint_publish"):
    value = {
        "schema_version": "mineagent.openevolve-event.v1",
        "kind": kind,
        "admission_id": str(uuid4()),
        "epoch_id": str(unit.epoch_id),
        "payload": {},
    }
    return canonical(
        {
            "event": value,
            "event_sha256": hashlib.sha256(canonical(value)).hexdigest(),
            "process_ticket": "a" * 64,
        }
    )


def test_actual_socketpair_exact_committed_ack(monkeypatch):
    unit = initial()
    raw, receipt = wire(unit), uuid4()
    result = {"published": True, "frontier": 1}
    calls = []
    monkeypatch.setattr(
        server,
        "authenticate_socket",
        lambda *a, **k: calls.append("authenticate") or object(),
    )

    def dispatch(*a, **k):
        assert calls == ["authenticate"]
        calls.append("commit")
        return receipt, result

    monkeypatch.setattr(server, "dispatch_event", dispatch)
    client, host = socket.socketpair()
    errors = []

    def run():
        try:
            unit._handle(host, "events")
        except BaseException as exc:  # noqa: BLE001 - capture thread test failure
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        client.sendall(struct.pack("!I", len(raw)) + raw)
        header = server.read_exact(client, 4, unit.running.deadline)
        answer = server.read_exact(
            client, struct.unpack("!I", header)[0], unit.running.deadline
        )
        assert answer == reply(parse_event(raw), receipt, result)
    finally:
        client.close()
        thread.join(3)
    assert not thread.is_alive() and not errors
    assert unit._final.is_set() and not unit._connections
    assert calls == ["authenticate", "commit"]


@pytest.mark.parametrize("size", [0, 1048577, 0xFFFFFFFF])
def test_oversized_header_never_authenticates(monkeypatch, size):
    unit = initial()
    monkeypatch.setattr(
        server,
        "authenticate_socket",
        lambda *a, **k: pytest.fail("authentication reached"),
    )
    client, host = socket.socketpair()
    try:
        client.sendall(struct.pack("!I", size))
        with pytest.raises(ValueError, match="frame bound"):
            unit._handle(host, "events")
        assert not unit._final.is_set()
    finally:
        client.close()
        host.close()


def test_handler_failure_has_no_success_ack(monkeypatch):
    unit = initial()
    monkeypatch.setattr(server, "authenticate_socket", lambda *a, **k: object())

    def fail(*a, **k):
        raise ValueError("actual transaction refused")

    monkeypatch.setattr(server, "dispatch_event", fail)
    client, host = socket.socketpair()
    try:
        raw = wire(unit)
        client.sendall(struct.pack("!I", len(raw)) + raw)
        with pytest.raises(ValueError, match="transaction refused"):
            unit._handle(host, "events")
        assert client.recv(1) == b"" and not unit._final.is_set()
    finally:
        client.close()
        host.close()


def test_disconnect_unblocks_reader_without_stop_or_receipt():
    unit = initial()
    client, host = socket.socketpair()
    unit._connections.add(host)
    try:
        unit.disconnect()
        assert client.recv(1) == b""
        assert not unit._final.is_set()
        assert not hasattr(unit, "stop")
    finally:
        client.close()
        host.close()
