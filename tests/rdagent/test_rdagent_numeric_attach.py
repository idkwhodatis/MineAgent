"""Pure socketpair/protocol tests; no Docker or hostile execution."""

import socket
import struct
import threading
import time

import pytest

from runtimes.factor_execution import attach_stream as transport

UPGRADE = (
    b"HTTP/1.1 101 UPGRADED\r\n"
    b"Content-Type: application/vnd.docker.multiplexed-stream\r\n"
    b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
)


def packet(stream=1, payload=b"opaque"):
    return struct.pack(">B3xI", stream, len(payload)) + payload


def reader_for(raw):
    client, server = socket.socketpair()
    server.sendall(raw)
    server.close()
    return transport.AttachStream(client)


def test_exact_upgrade():
    transport.parse_upgrade(UPGRADE)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 8193,
        UPGRADE[:-1],
        UPGRADE.replace(b"101", b"200"),
        UPGRADE.replace(b"multiplexed-stream", b"raw-stream"),
        UPGRADE.replace(b"Upgrade: tcp", b"Upgrade: websocket"),
        UPGRADE.replace(b"Connection: Upgrade", b"Connection: close"),
        UPGRADE.replace(b"Upgrade: tcp", b"Upgrade: tcp\r\nUpgrade: tcp"),
        UPGRADE.replace(b"Upgrade: tcp", b" Upgrade: tcp"),
        UPGRADE.replace(b"Upgrade: tcp", b"Transfer-Encoding: chunked\r\nUpgrade: tcp"),
        UPGRADE.replace(b"Upgrade: tcp", b"Content-Length: 1\r\nUpgrade: tcp"),
        UPGRADE.replace(b"Upgrade: tcp", b"Upgrade: \xff"),
        UPGRADE.replace(b"Upgrade: tcp", b"missing-colon"),
    ],
)
def test_invalid_upgrade_is_closed(raw):
    with pytest.raises(transport.AttachProtocolError):
        transport.parse_upgrade(raw)


def test_multiplexed_stdout_stderr_and_clean_eof():
    with reader_for(
        packet(1, b"first") + packet(2, b"diagnostic") + packet(1, b"second")
    ) as reader:
        assert reader.collect_until_eof(deadline=time.monotonic() + 1) == {
            1: b"firstsecond",
            2: b"diagnostic",
        }
        assert reader.read_frame(deadline=time.monotonic() + 1) is None


@pytest.mark.parametrize(
    "raw",
    [
        b"\1",
        packet()[:7],
        packet()[:-1],
        packet(0),
        packet(3),
        packet(255),
        packet(payload=b""),
        b"\1\1\0\0" + (1).to_bytes(4, "big") + b"x",
        b"\1\0\0\0" + (transport.MAX_FRAME_BYTES + 1).to_bytes(4, "big"),
    ],
)
def test_bad_or_truncated_frame_poisoned(raw):
    with reader_for(raw) as reader:
        with pytest.raises(transport.AttachProtocolError):
            reader.read_frame(deadline=time.monotonic() + 1)
        with pytest.raises(transport.AttachProtocolError, match="closed"):
            reader.read_frame(deadline=time.monotonic() + 1)


def test_aggregate_byte_cap(monkeypatch):
    monkeypatch.setattr(transport, "MAX_TOTAL_BYTES", 8)
    with reader_for(packet(payload=b"12345") + packet(payload=b"6789")) as reader:
        assert reader.read_frame(deadline=time.monotonic() + 1) == (1, b"12345")
        with pytest.raises(transport.AttachProtocolError, match="aggregate"):
            reader.read_frame(deadline=time.monotonic() + 1)


def test_aggregate_frame_cap(monkeypatch):
    monkeypatch.setattr(transport, "MAX_FRAMES", 1)
    with reader_for(packet() + packet()) as reader:
        reader.read_frame(deadline=time.monotonic() + 1)
        with pytest.raises(transport.AttachProtocolError, match="aggregate"):
            reader.read_frame(deadline=time.monotonic() + 1)


def test_unreadable_socket_hits_original_deadline_and_closes():
    client, server = socket.socketpair()
    try:
        with transport.AttachStream(client) as reader:
            with pytest.raises(TimeoutError):
                reader.read_frame(deadline=time.monotonic() + 0.01)
            with pytest.raises(transport.AttachProtocolError, match="closed"):
                reader.read_frame(deadline=time.monotonic() + 1)
    finally:
        server.close()


@pytest.mark.parametrize("deadline", [True, None, float("inf"), float("nan"), -1])
def test_invalid_or_expired_deadline(deadline):
    with reader_for(packet()) as reader, pytest.raises((ValueError, TimeoutError)):
        reader.read_frame(deadline=deadline)


@pytest.mark.parametrize("cid", ["", "a" * 63, "A" * 64, "a" * 63 + "/", True])
def test_connect_requires_exact_container_identity(cid):
    with pytest.raises(ValueError):
        transport.AttachStream.connect(cid, deadline=time.monotonic() + 1)


def test_prestart_handshake_retains_first_frame_and_fixed_local_route(monkeypatch):
    client, server = socket.socketpair()
    paths = []
    requests = []

    class Connection:
        def __getattr__(self, name):
            return getattr(client, name)

        def connect(self, path):
            paths.append(path)

    def peer():
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += server.recv(4096)
        requests.append(raw)
        server.sendall(UPGRADE + packet(payload=b"already attached"))
        server.close()

    thread = threading.Thread(target=peer)
    thread.start()
    monkeypatch.setattr(transport.socket, "socket", lambda *_: Connection())
    with transport.AttachStream.connect(
        "a" * 64, deadline=time.monotonic() + 1
    ) as reader:
        assert reader.collect_until_eof(deadline=time.monotonic() + 1) == {
            1: b"already attached",
            2: b"",
        }
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert paths == ["/var/run/docker.sock"]
    assert requests[0].startswith(b"POST /v1.47/containers/" + b"a" * 64 + b"/attach?")
    assert b"stdin=0&logs=0 HTTP/1.1" in requests[0]


def test_partial_frame_arrival_is_reassembled():
    client, server = socket.socketpair()
    raw = packet(payload=b"fragmented")

    def peer():
        for chunk in (raw[:3], raw[3:9], raw[9:]):
            server.sendall(chunk)
            time.sleep(0.001)
        server.close()

    thread = threading.Thread(target=peer)
    thread.start()
    with transport.AttachStream(client) as reader:
        assert reader.collect_until_eof(deadline=time.monotonic() + 1) == {
            1: b"fragmented",
            2: b"",
        }
    thread.join(timeout=1)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "reply", [b"HTTP/1.1 500 ERROR\r\n\r\n", b"HTTP/1.1 101", b"x" * 8192]
)
def test_failed_handshake_closes_socket(monkeypatch, reply):
    client, server = socket.socketpair()

    class Connection:
        def __getattr__(self, name):
            return getattr(client, name)

        def connect(self, path):
            assert path == transport.SOCKET_PATH

    def peer():
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = server.recv(4096)
            if not chunk:
                return
            raw += chunk
        server.sendall(reply)
        server.close()

    thread = threading.Thread(target=peer)
    thread.start()
    monkeypatch.setattr(transport.socket, "socket", lambda *_: Connection())
    with pytest.raises(transport.AttachProtocolError):
        transport.AttachStream.connect("b" * 64, deadline=time.monotonic() + 1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert client.fileno() == -1


def test_exact_frame_byte_cap():
    payload = b"x" * transport.MAX_FRAME_BYTES
    with reader_for(packet(payload=payload)) as reader:
        assert reader.read_frame(deadline=time.monotonic() + 1) == (1, payload)
        assert reader.read_frame(deadline=time.monotonic() + 1) is None


def test_initial_buffer_cap_closes_socket():
    client, server = socket.socketpair()
    try:
        with pytest.raises(transport.AttachProtocolError):
            transport.AttachStream(client, b"x" * (transport.MAX_FRAME_BYTES + 9))
        assert client.fileno() == -1
    finally:
        server.close()


def test_numeric_cap_is_distinct_and_does_not_mutate_proof(monkeypatch):
    from runtimes.hostile_deadline import attach_stream as proof

    assert transport.MAX_TOTAL_BYTES == 17 * 1024 * 1024
    assert proof.MAX_TOTAL_BYTES == 5 * 1024 * 1024
    assert transport.MAX_FRAME_BYTES == proof.MAX_FRAME_BYTES == 65536
    assert transport.SOCKET_PATH == proof.SOCKET_PATH == "/var/run/docker.sock"
    monkeypatch.setattr(transport, "MAX_TOTAL_BYTES", 1)
    assert proof.MAX_TOTAL_BYTES == 5 * 1024 * 1024


@pytest.mark.parametrize("extra_byte", [False, True])
def test_real_fixed_numeric_aggregate_boundary(extra_byte):
    """Stream the real 17 MiB cap using bounded socketpair chunks, not patched limits."""
    client, server = socket.socketpair()
    errors = []
    payload = b"x" * transport.MAX_FRAME_BYTES
    count = transport.MAX_TOTAL_BYTES // len(payload)
    assert count * len(payload) == transport.MAX_TOTAL_BYTES

    def peer():
        try:
            for _ in range(count):
                server.sendall(packet(payload=payload))
            if extra_byte:
                server.sendall(packet(payload=b"!"))
        except (BrokenPipeError, ConnectionResetError):
            if not extra_byte:
                errors.append("unexpected closed reader")
        finally:
            server.close()

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        with transport.AttachStream(client) as reader:
            deadline = time.monotonic() + 5
            for _ in range(count):
                assert reader.read_frame(deadline=deadline) == (1, payload)
            assert reader._total == transport.MAX_TOTAL_BYTES
            if extra_byte:
                with pytest.raises(transport.AttachProtocolError, match="aggregate"):
                    reader.read_frame(deadline=deadline)
                assert client.fileno() == -1
            else:
                assert reader.read_frame(deadline=deadline) is None
    finally:
        client.close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
