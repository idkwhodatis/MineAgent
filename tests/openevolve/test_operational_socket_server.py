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
    monkeypatch.setattr(server, "final_checkpoint_committed", lambda *a, **k: True)
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


def test_real_unix_server_acks_last_terminal_then_waits_for_distinct_final_save(
    monkeypatch, tmp_path
):
    unit = initial()
    unit.frontier = 2
    unit.running.deadline = time.monotonic() + 5
    first_client, first_host = socket.socketpair()
    unit.running.connection = first_host
    # Real socketpair framing; only accept readiness is an explicit double
    # because this executor forbids creating listening sockets.
    event_listener, event_other = socket.socketpair()
    broker_listener, broker_other = socket.socketpair()
    final_client, final_host = socket.socketpair()
    unit.running.listener, unit.running.broker_listener = (
        event_listener,
        broker_listener,
    )
    ready = threading.Event()

    class Selector:
        delivered = False

        def register(self, *args):
            pass

        def select(self, timeout):
            if ready.wait(timeout) and not self.delivered:
                self.delivered = True
                return [
                    (
                        NS(
                            fileobj=NS(accept=lambda: (final_host, None)), data="events"
                        ),
                        None,
                    )
                ]
            return []

        def close(self):
            pass

    monkeypatch.setattr(server.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(server, "authenticate_socket", lambda *a, **k: object())
    published = []
    tokens = {str(uuid4()): ordinal for ordinal in (3, 4)}

    def dispatch(*a, **kwargs):
        event = kwargs["event"]
        published.append(tokens[event.payload["token"]])
        return uuid4(), {"published": True, "frontier": 2}

    monkeypatch.setattr(server, "dispatch_event", dispatch)
    monkeypatch.setattr(
        server,
        "final_checkpoint_committed",
        lambda *a, **kwargs: tokens[kwargs["event"].payload["token"]] == 4,
    )
    outcome, errors = [], []

    def serve():
        try:
            outcome.append(unit.serve())
        except BaseException as error:  # noqa: BLE001 - capture test thread failure
            errors.append(error)

    def exchange(channel, ordinal):
        original = parse_event(wire(unit))
        document = {
            "schema_version": "mineagent.openevolve-event.v1",
            "admission_id": str(original.admission_id),
            "epoch_id": str(unit.epoch_id),
            "kind": "checkpoint_publish",
            "payload": {
                "token": next(
                    token for token, value in tokens.items() if value == ordinal
                ),
                "frontier": 2,
                "state_sha256": "b" * 64,
                "path": "/work/checkpoints/"
                + next(token for token, value in tokens.items() if value == ordinal),
            },
        }
        raw = canonical(
            {
                "event": document,
                "event_sha256": hashlib.sha256(canonical(document)).hexdigest(),
                "process_ticket": "a" * 64,
            }
        )
        channel.sendall(struct.pack("!I", len(raw)) + raw)
        size = struct.unpack(
            "!I", server.read_exact(channel, 4, unit.running.deadline)
        )[0]
        return server.read_exact(channel, size, unit.running.deadline)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert b'"published":true' in exchange(first_client, 3)
        assert thread.is_alive() and not unit._final.is_set() and not outcome
        ready.set()
        assert b'"published":true' in exchange(final_client, 4)
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert published == [3, 4] and outcome[0]["final_frontier"] == 2
    finally:
        unit.disconnect()
        first_client.close()
        for channel in (
            event_listener,
            event_other,
            broker_listener,
            broker_other,
            final_client,
            final_host,
        ):
            channel.close()
        thread.join(6)


def test_final_checkpoint_revalidation_failure_emits_no_ack(monkeypatch):
    unit = initial()
    monkeypatch.setattr(server, "authenticate_socket", lambda *a, **k: object())
    monkeypatch.setattr(
        server,
        "dispatch_event",
        lambda *a, **k: (uuid4(), {"published": True, "frontier": 1}),
    )

    def failed_commit(*a, **k):
        raise ValueError("fresh checkpoint transaction refused")

    monkeypatch.setattr(server, "final_checkpoint_committed", failed_commit)
    client, host = socket.socketpair()
    try:
        raw = wire(unit)
        client.sendall(struct.pack("!I", len(raw)) + raw)
        with pytest.raises(ValueError, match="fresh checkpoint transaction refused"):
            unit._handle(host, "events")
        assert client.recv(1) == b"" and not unit._final.is_set()
        assert unit.progress() == {
            "requests": 0,
            "acknowledgements": 0,
            "last_event": "checkpoint_publish",
            "active_handlers": 0,
        }
    finally:
        client.close()
        host.close()
