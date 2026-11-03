"""Synthetic wire tests only: no daemon or elapsed-performance claims."""

import io
import json
import socket
import stat
import struct
from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational import docker_read_transport as m

PATH = m.API + "/containers/" + "a" * 64 + "/json"


def response(body=b'{"Id":"ok"}', headers=None, status=b"200 OK"):
    if headers is None:
        headers = (
            b"Content-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
        )
    return b"HTTP/1.1 " + status + b"\r\n" + headers + b"\r\n\r\n" + body


class Connection:
    def __init__(self, raw):
        self.raw = io.BytesIO(raw)
        self.closed = False
        self.requests = []
        self.timeouts = []
        self.peer = (42, 0, 0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        assert 0 < timeout <= 10
        self.timeouts.append(timeout)

    def connect(self, path):
        assert path == "/var/run/docker.sock"

    def getsockopt(self, level, option, size):
        assert (level, option, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack("3i", *self.peer)

    def sendall(self, raw):
        self.requests.append(raw)

    def recv_into(self, target):
        return self.raw.readinto(target)


@pytest.fixture
def wire(monkeypatch):
    clock = [100.0]
    connections = []
    info = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=999, st_dev=1, st_ino=2
    )
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.os, "lstat", lambda path: info)

    def install(raw):
        connection = Connection(raw)
        connections.append(connection)
        monkeypatch.setattr(m.socket, "socket", lambda *args: connection)
        return connection

    return SimpleNamespace(
        install=install, clock=clock, info=info, connections=connections
    )


def test_fixed_get_and_close(wire):
    connection = wire.install(response())
    assert m._get(PATH, 108) == {"Id": "ok"}
    assert connection.closed
    assert connection.requests == [
        (
            f"GET {PATH} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
        ).encode()
    ]


def test_chunked(wire):
    wire.install(
        response(
            b'B\r\n{"Id":"ok"}\r\n0\r\n\r\n',
            b"Content-Type: application/json\r\nTransfer-Encoding: chunked",
        )
    )
    assert m._get(PATH, 108) == {"Id": "ok"}


@pytest.mark.parametrize(
    "raw",
    [
        response(status=b"301 Redirect"),
        response(status=b"404 Not Found"),
        response(status=b"500 Error"),
        response(b"{}", b"Content-Type: application/json\r\nContent-Length: 3"),
        response(
            b"{}",
            b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2",
        ),
        response(
            b"{}",
            b"Content-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked",
        ),
        response(b"{}", b"Content-Type: application/json\r\nTransfer-Encoding: gzip"),
        response(b"{}", b"Content-Type: text/plain\r\nContent-Length: 2"),
        response(
            b"{}",
            b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Encoding: gzip",
        ),
        response(b"{}", b"Content-Type: application/json"),
        response(b"{}", b"Content-Type: application/json\r\nContent-Length: -1"),
        response(b"{}", b"Content-Type: application/json\r\nContent-Length: 65537"),
        response(b"{}", b"Content-Type: application/json\r\nX-Big: " + b"x" * 90000),
        response(b'{"a":1,"a":2}'),
        response(b'{"a":NaN}'),
        response(b"[]"),
        response(b"{}junk"),
        response(b"\xff"),
        response(
            b"1\r\n{\r\n0\r\n\r\n",
            b"Content-Type: application/json\r\nTransfer-Encoding: chunked",
        ),
    ],
)
def test_invalid_response_closed(wire, raw):
    connection = wire.install(raw)
    with pytest.raises(ProcessError, match="oe_controller_daemon_identity"):
        m._get(PATH, 108)
    assert connection.closed


@pytest.mark.parametrize(
    "path",
    [
        "http://evil/json",
        "/version",
        "/v1.45/containers/a/kill",
        PATH + "?x=1",
        PATH.replace("a" * 64, "../escape"),
        PATH.replace("v1.45", "v1.47"),
    ],
)
def test_path_allowlist_before_connection(wire, path):
    connection = wire.install(response())
    with pytest.raises(ProcessError):
        m._get(path, 108)
    assert not connection.requests


@pytest.mark.parametrize(
    "change", ["uid", "world", "symlink", "inode", "peer", "peer_pid", "expired"]
)
def test_identity_and_deadline_fail_closed(wire, change):
    connection = wire.install(response())
    if change == "uid":
        wire.info.st_uid = 1000
    elif change == "world":
        wire.info.st_mode |= 0o006
    elif change == "symlink":
        wire.info.st_mode = stat.S_IFLNK | 0o777
    elif change == "peer":
        connection.peer = (42, 1000, 1000)
    elif change == "peer_pid":
        connection.peer = (0, 0, 0)
    elif change == "expired":
        wire.clock[0] = 108
    elif change == "inode":
        old = connection.sendall

        def swap(raw):
            old(raw)
            wire.info.st_ino += 1

        connection.sendall = swap
    with pytest.raises(ProcessError):
        m._get(PATH, 108)


def test_slow_response_uses_absolute_deadline(wire):
    connection = wire.install(response())
    old = connection.recv_into

    def slow(target):
        result = old(target[:1])
        wire.clock[0] += 1
        return result

    connection.recv_into = slow
    with pytest.raises(ProcessError):
        m._get(PATH, 108)
    assert connection.closed
    assert wire.clock[0] <= 108


def test_inspect_compact_but_cli_too_large_rejected(wire):
    raw = json.dumps({str(i): [0] for i in range(2000)}, separators=(",", ":")).encode()
    assert len(raw) < 65536 < len(json.dumps([json.loads(raw)], indent=4))
    wire.install(response(raw))
    with pytest.raises(ProcessError):
        m._get(PATH, 108)


def test_no_connection_reuse(wire):
    first = wire.install(response())
    m._get(PATH, 108)
    second = wire.install(response())
    m._get(PATH, 108)
    assert first is not second and first.closed and second.closed
    assert len(first.requests) == len(second.requests) == 1


def test_parse_completion_must_still_meet_deadline(wire, monkeypatch):
    connection = wire.install(response())
    original = m.json.loads

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        wire.clock[0] = 109
        return value

    monkeypatch.setattr(m.json, "loads", delayed)
    with pytest.raises(ProcessError):
        m._get(PATH, 108)
    assert connection.closed


def test_read_error_is_closed_and_no_fallback(wire):
    connection = wire.install(response())

    def denied(target):
        raise PermissionError("private failure detail")

    connection.recv_into = denied
    with pytest.raises(ProcessError) as error:
        m._get(PATH, 108)
    assert str(error.value) == "oe_controller_daemon_identity"
    assert connection.closed and len(connection.requests) == 1


@pytest.mark.parametrize(
    "chunk",
    [
        b"2\r\n{}XX0\r\n\r\n",
        b"2\r\n{}\r\n0\r\n",
        b"+2\r\n{}\r\n0\r\n\r\n",
        b"2\n{}\r\n0\r\n\r\n",
        b"2\r\n{}\r\n0\r\nContent-Length: 0\r\n\r\n",
        b"2;extension=1\r\n{}\r\n0\r\n\r\n",
    ],
)
def test_strict_chunk_framing(wire, chunk):
    wire.install(
        response(chunk, b"Content-Type: application/json\r\nTransfer-Encoding: chunked")
    )
    with pytest.raises(ProcessError):
        m._get(PATH, 108)


@pytest.mark.parametrize(
    "raw",
    [
        response(b"{}") + b"ignored",
        response(b"{}").replace(b"HTTP/1.1", b"HTTP/1.9"),
        response(b"{}").replace(b"\r\n", b"\n"),
        response(b"{}").replace(b"Content-Length", b" Content-Length"),
    ],
)
def test_strict_status_headers_and_eof(wire, raw):
    wire.install(raw)
    with pytest.raises(ProcessError):
        m._get(PATH, 108)
