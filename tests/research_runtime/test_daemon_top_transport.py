"""Real fake-Unix-daemon transport tests, not a Docker containment proof."""

import fcntl
import json
import os
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational import daemon_identity as identity
from runtimes.rdagent_operational import daemon_identity as rd_identity
from runtimes.research_supervisor import daemon_top as module

CID = "d" * 64
DOCUMENT = {
    "Titles": ["PID", "UID", "PPID", "PIDNS", "COMMAND"],
    "Processes": [
        ["100", "0", "1", "4026532777", "python"],
        ["101", "65533", "100", "4026532777", "python"],
    ],
}
BODY = json.dumps(DOCUMENT, separators=(",", ":")).encode()


def parse_top(raw):
    """Every accepted projection must agree through both unchanged parsers."""
    oe_rows = identity.parse_top(raw)
    rd_rows = rd_identity.parse_top(raw)
    assert oe_rows == rd_rows
    return rd_rows


def response(body=BODY, *, headers=None, status=b"HTTP/1.1 200 OK"):
    if headers is None:
        headers = [b"Content-Length: " + str(len(body)).encode()]
    return (
        b"\r\n".join(
            [status, b"Content-Type: application/json", b"Connection: close", *headers]
        )
        + b"\r\n\r\n"
        + body
    )


@contextmanager
def daemon(monkeypatch, payload, *, fragment=8192, hold=False, drip=0):
    """Serve any connection, so an accidental retry is observable, not hidden."""
    requests = []
    stopped = threading.Event()
    errors = []
    with tempfile.TemporaryDirectory(prefix="host-top-", dir="/tmp") as root:
        path = str(Path(root) / "daemon.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen()
        listener.settimeout(0.05)
        monkeypatch.setattr(module, "SOCKET_PATH", path)

        def serve():
            while not stopped.is_set():
                try:
                    channel, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with channel:
                    channel.settimeout(1)
                    request = bytearray()
                    try:
                        while b"\r\n\r\n" not in request:
                            part = channel.recv(4096)
                            if not part:
                                break
                            request.extend(part)
                            assert len(request) <= 4096
                        requests.append(bytes(request))
                        for start in range(0, len(payload), fragment):
                            channel.sendall(payload[start : start + fragment])
                            if drip and stopped.wait(drip):
                                break
                        if hold:
                            stopped.wait(1)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except (OSError, AssertionError) as error:
                        errors.append(error)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield requests
        finally:
            stopped.set()
            listener.close()
            thread.join(2)
            assert not thread.is_alive()
            assert not errors


def read():
    return module.read_top(CID, deadline=time.monotonic() + 1)


def test_exact_endpoint_argv_and_cli_tabwriter_bytes(monkeypatch):
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "DOCKER_API_VERSION",
        "DOCKER_CONFIG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "XDG_RUNTIME_DIR",
    ):
        monkeypatch.setenv(name, "untrusted-must-not-be-read")
    with daemon(monkeypatch, response(), fragment=1) as requests:
        raw = read()
    assert len(requests) == 1
    assert (
        requests[0]
        == (
            f"GET /containers/{CID}/top?ps_args=-eo+pid%2Cuid%2Cppid%2Cpidns%2Ccomm HTTP/1.1\r\n"
            "Host: localhost\r\nAccept: application/json\r\n"
            "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
        ).encode()
    )
    # CLI uses (minwidth=20, tabwidth=1, padding=3, padchar=' ', flags=0),
    # not compact single-space columns. Only the first four cells are padded.
    assert raw == (
        b"PID                 UID                 PPID                PIDNS               COMMAND\n"
        b"100                 0                   1                   4026532777          python\n"
        b"101                 65533               100                 4026532777          python\n"
    )
    assert parse_top(raw) == {
        100: (0, 1, 4026532777),
        101: (65533, 100, 4026532777),
    }


def test_top_wire_size_and_cli_padding_maximal_accepted_fields():
    document = {
        "Titles": module.TITLES,
        "Processes": [
            [str(10**19 + i), "9" * 20, "8" * 20, "7" * 20, "<" * 64] for i in range(64)
        ],
    }
    # encoding/json's default HTML escaping, unlike json.dumps, expands '<'.
    raw = json.dumps(document, separators=(",", ":")).replace("<", r"\u003c").encode()
    assert 16384 < len(raw) < module.MAX_JSON_BYTES
    text = module._cli_text(raw)
    assert len(text) == 10148  # 4*23 columns + last cell + newline, including header.
    assert text.splitlines()[1] == (
        str(10**19).encode()
        + b"   "
        + b"9" * 20
        + b"   "
        + b"8" * 20
        + b"   "
        + b"7" * 20
        + b"   "
        + b"<" * 64
    )
    assert len(parse_top(text)) == 64


def test_cli_equivalent_size_not_normalized_size():
    document = {**DOCUMENT, "Processes": [["1", "2", "3", "4", "x" * 16300]]}
    with pytest.raises(module.DaemonTopError):
        module._cli_text(json.dumps(document).encode())


@pytest.mark.parametrize("framing", ["chunked", "eof"])
def test_valid_chunked_and_explicit_close_eof_framing(monkeypatch, framing):
    if framing == "chunked":
        payload = response(
            b"".join(b"1\r\n" + bytes([c]) + b"\r\n" for c in BODY) + b"0\r\n\r\n",
            headers=[b"Transfer-Encoding: chunked"],
        )
    else:
        payload = response(headers=[])
    with daemon(monkeypatch, payload, fragment=7) as requests:
        assert parse_top(read())[101] == (65533, 100, 4026532777)
    assert len(requests) == 1


HTTP_FAILURES = [
    response(status=b"HTTP/1.1 404 Not Found"),
    response(status=b"HTTP/1.1 500 Internal Server Error"),
    response(status=b"HTTP/1.1 302 Found"),
    response(status=b"HTTP/1.1 101 Switching Protocols"),
    response(status=b"HTTP/1.0 200 OK"),
    response(headers=[b"Location: http://untrusted/", b"Content-Length: 1"]),
    response().replace(b"application/json", b"text/plain"),
    response().replace(b"Connection: close", b"Connection: keep-alive"),
    response(headers=[b"Content-Encoding: gzip", b"Content-Length: 1"]),
    response(headers=[b"Content-Length: 1", b"Content-Length: 1"]),
    response(headers=[b"content-length: 1", b"Content-Length: 1"]),
    # Fully valid body and framing except for the duplicate. Dropping duplicate
    # detection must make these pass, unlike a separately wrong length/body.
    response(headers=[f"Content-Length: {len(BODY)}".encode()] * 2),
    response(
        headers=[
            b"Content-Type: application/json",
            f"Content-Length: {len(BODY)}".encode(),
        ]
    ),
    response(
        headers=[
            b"X-Value: valid",
            b"x-value: valid",
            f"Content-Length: {len(BODY)}".encode(),
        ]
    ),
    response(headers=[b"Content-Length: 1", b"Transfer-Encoding: chunked"]),
    response(headers=[b"Content-Length: -1"]),
    response(headers=[b"Content-Length: +1"]),
    response(headers=[b"Content-Length: 1, 1"]),
    response(headers=[b"Content-Length: 65537"]),
    response(headers=[b"Content-Length: " + b"9" * 100]),
    response(headers=[b"Transfer-Encoding: gzip, chunked"]),
    response(headers=[b"Trailer: X-Extra", b"Content-Length: 1"]),
    response(headers=[b"Upgrade: tcp", b"Content-Length: 1"]),
    response(headers=[b" folded: 1", b"Content-Length: 1"]),
    response(headers=[b"Bad: \x00", b"Content-Length: 1"]),
    response(headers=[b"Bad: \xff", b"Content-Length: 1"]),
    response(headers=[b"Bad Name: 1", b"Content-Length: 1"]),
    response(headers=[b"Bad-No-Colon", b"Content-Length: 1"]),
    response()[:-1],
    response() + b"x",
    response() + response(),
    response(b"1\r\nx\r\n0\r\nX: y\r\n\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"1;ext=yes\r\nx\r\n0\r\n\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"z\r\nx\r\n0\r\n\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"10001\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"1\r\nx!!0\r\n\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"1\r\nx\r\n", headers=[b"Transfer-Encoding: chunked"]),
    response(b"0\r\n\r\nx", headers=[b"Transfer-Encoding: chunked"]),
    response(b"1\r\nx\r\n" * 1025, headers=[b"Transfer-Encoding: chunked"]),
    response(b"x" * 65537, headers=[]),
    response(headers=[]).replace(b"Connection: close\r\n", b""),
    b"HTTP/1.1 200 OK\r\nLong: " + b"x" * 8192,
    b"HTTP/1.1 200 OK\r\n\r",
    b"",
]


@pytest.mark.parametrize(
    "payload", HTTP_FAILURES, ids=[f"http-{n}" for n in range(len(HTTP_FAILURES))]
)
def test_http_failures_close_without_retry_or_cli_fallback(monkeypatch, payload):
    fallback = Mock(side_effect=AssertionError("CLI fallback is forbidden"))
    monkeypatch.setattr(identity.DockerSupervisor, "_command", fallback)
    with daemon(monkeypatch, payload) as requests, pytest.raises(module.DaemonTopError):
        read()
    assert len(requests) == 1
    fallback.assert_not_called()


JSON_FAILURES = [
    b"",
    b"{}",
    b"[]",
    b"null",
    b"false",
    b"NaN",
    b"\xff",
    BODY + b"x",
    BODY + BODY,
    b'{"Titles":[],"Titles":[],"Processes":[]}',
    # Without duplicate-key rejection these decode to the fully valid DOCUMENT.
    BODY[:-1] + b',"Titles":' + json.dumps(DOCUMENT["Titles"]).encode() + b"}",
    BODY[:-1] + b',"Processes":' + json.dumps(DOCUMENT["Processes"]).encode() + b"}",
    b"[" * 4000 + b"]" * 4000,
    BODY.decode().encode("utf-16"),
    json.dumps(
        {**DOCUMENT, "Titles": ["PID", "UID", "PPID", "NS", "COMMAND"]}
    ).encode(),
    json.dumps({**DOCUMENT, "Extra": 1}).encode(),
    json.dumps({**DOCUMENT, "Processes": None}).encode(),
    json.dumps({**DOCUMENT, "Processes": []}).encode(),
    json.dumps({**DOCUMENT, "Processes": DOCUMENT["Processes"] * 33}).encode(),
    json.dumps({**DOCUMENT, "Processes": ["not a row"]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1"] * 4]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1"] * 6]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [[1, "2", "3", "4", "python"]]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [[True, "2", "3", "4", "python"]]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1", "2", "3", "4", "two words"]]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1", "2", "3", "4", ""]]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1", "2", "3", "4", "\n"]]}).encode(),
    json.dumps({**DOCUMENT, "Processes": [["1", "2", "3", "4", "é"]]}).encode(),
]


@pytest.mark.parametrize(
    "body", JSON_FAILURES, ids=[f"json-{n}" for n in range(len(JSON_FAILURES))]
)
def test_json_and_projection_failures(monkeypatch, body):
    with (
        daemon(monkeypatch, response(body)) as requests,
        pytest.raises(module.DaemonTopError),
    ):
        read()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "field,value", [(0, "0"), (0, "-1"), (1, "root"), (3, "0"), (4, "x" * 65)]
)
def test_projection_still_uses_existing_identity_parser(monkeypatch, field, value):
    document = json.loads(BODY)
    document["Processes"][0][field] = value
    with daemon(monkeypatch, response(json.dumps(document).encode())):
        raw = read()
    for parser in (identity.parse_top, rd_identity.parse_top):
        with pytest.raises(ProcessError):
            parser(raw)


@pytest.mark.parametrize(
    "payload,fragment,drip,hold",
    [
        (b"", 8192, 0, True),
        (b"HTTP/1.1 200 OK\r\n", 1, 0.02, False),
        (response(), 1, 0.02, False),
        (response(), 8192, 0, True),
    ],
)
def test_absolute_deadline_covers_headers_body_and_required_eof(
    monkeypatch, payload, fragment, drip, hold
):
    with daemon(
        monkeypatch, payload, fragment=fragment, drip=drip, hold=hold
    ) as requests:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            module.read_top(CID, deadline=started + 0.06)
        assert time.monotonic() - started < 0.5
    assert len(requests) == 1


@pytest.mark.parametrize(
    "cid,deadline",
    [("name", 60), (CID, True), (CID, float("inf")), (CID, float("nan"))],
)
def test_invalid_context_rejected_before_socket(monkeypatch, cid, deadline):
    factory = Mock(side_effect=AssertionError("no socket"))
    monkeypatch.setattr(module.socket, "socket", factory)
    with pytest.raises(module.DaemonTopError):
        module.read_top(cid, deadline=deadline)
    factory.assert_not_called()


def test_expired_deadline_rejected_before_socket(monkeypatch):
    factory = Mock(side_effect=AssertionError("no socket"))
    monkeypatch.setattr(module.socket, "socket", factory)
    with pytest.raises(TimeoutError):
        module.read_top(CID, deadline=time.monotonic() - 1)
    factory.assert_not_called()


def test_each_call_connects_and_observes_fresh(monkeypatch):
    with daemon(monkeypatch, response()) as requests:
        first = read()
        second = read()
    assert first == second
    assert len(requests) == 2


def test_parse_finishing_after_original_deadline_is_rejected(monkeypatch):
    original = module._cli_text

    def slow(raw):
        result = original(raw)
        time.sleep(0.08)
        return result

    monkeypatch.setattr(module, "_cli_text", slow)
    with daemon(monkeypatch, response()) as requests, pytest.raises(TimeoutError):
        module.read_top(CID, deadline=time.monotonic() + 0.06)
    assert len(requests) == 1


class MemorySocket:
    """No OS socket: independent parser coverage when AF_UNIX is unavailable."""

    def __init__(self, payload, fragment=7):
        self.payload = payload
        self.fragment = fragment
        self.request = bytearray()
        self.closed = False
        self.timeouts = []
        self.endpoints = []

    def recv(self, maximum):
        count = min(maximum, self.fragment)
        part, self.payload = self.payload[:count], self.payload[count:]
        return part

    def send(self, payload):
        count = min(len(payload), self.fragment)
        self.request.extend(payload[:count])
        return count

    def settimeout(self, value):
        self.timeouts.append(value)

    def setblocking(self, value):
        assert value is False

    def connect(self, endpoint):
        self.endpoints.append(endpoint)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


@pytest.fixture
def memory_wire(monkeypatch):
    class MemorySelector:
        def register(self, connection, event):
            self.key = SimpleNamespace(fileobj=connection)
            self.event = event

        def select(self, timeout):
            assert timeout > 0
            return [(self.key, self.event)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(module.selectors, "DefaultSelector", MemorySelector)

    def make(payload):
        return module._Stream(MemorySocket(payload), time.monotonic() + 1)

    return make


@pytest.mark.parametrize(
    "payload", HTTP_FAILURES, ids=[f"http-{n}" for n in range(len(HTTP_FAILURES))]
)
def test_memory_http_protocol_faults(memory_wire, payload):
    with pytest.raises(module.DaemonTopError):
        module._cli_text(module._body(memory_wire(payload)))


@pytest.mark.parametrize(
    "body", JSON_FAILURES, ids=[f"json-{n}" for n in range(len(JSON_FAILURES))]
)
def test_memory_json_projection_faults(body):
    with pytest.raises(module.DaemonTopError):
        module._cli_text(body)


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_memory_valid_http_framing(memory_wire, framing):
    if framing == "chunked":
        payload = response(
            f"{len(BODY):x}\r\n".encode() + BODY + b"\r\n0\r\n\r\n",
            headers=[b"Transfer-Encoding: chunked"],
        )
    elif framing == "eof":
        payload = response(headers=[])
    else:
        payload = response()
    assert module._body(memory_wire(payload)) == BODY


def test_memory_one_request_exact_socket_and_ten_second_ceiling(
    monkeypatch, memory_wire
):
    channel = MemorySocket(response())
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10)
    monkeypatch.setenv("DOCKER_HOST", "unix:///untrusted.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "untrusted")
    monkeypatch.setenv("DOCKER_API_VERSION", "99.99")
    raw = module.read_top(CID, deadline=600)
    assert parse_top(raw)[100] == (0, 1, 4026532777)
    factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
    assert channel.endpoints == ["/var/run/docker.sock"]
    assert channel.timeouts == [10]
    assert bytes(channel.request).startswith(
        f"GET /containers/{CID}/top?{module.PS_QUERY} HTTP/1.1\r\n".encode()
    )
    assert channel.closed


def test_memory_original_deadline_smaller_than_ceiling(monkeypatch, memory_wire):
    channel = MemorySocket(response())
    monkeypatch.setattr(module.socket, "socket", Mock(return_value=channel))
    monkeypatch.setattr(module.time, "monotonic", lambda: 10)
    module.read_top(CID, deadline=10.25)
    assert channel.timeouts == [0.25]


def test_memory_failure_closes_without_retry(monkeypatch, memory_wire):
    channel = MemorySocket(response(status=b"HTTP/1.1 500 Error"))
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    with pytest.raises(module.DaemonTopError):
        read()
    assert factory.call_count == 1
    assert channel.closed


def test_memory_absolute_deadline_does_not_restart_for_fragments(
    monkeypatch, memory_wire
):
    channel = MemorySocket(response(), fragment=1)
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    clock = iter([10 + i / 100 for i in range(1000)])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        module.read_top(CID, deadline=10.5)
    assert factory.call_count == 1
    assert channel.closed


def test_memory_deadline_rechecked_after_parse(monkeypatch, memory_wire):
    now = [10]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        module.socket, "socket", Mock(return_value=MemorySocket(response()))
    )
    original = module._cli_text

    def parse(raw):
        result = original(raw)
        now[0] = 11
        return result

    monkeypatch.setattr(module, "_cli_text", parse)
    with pytest.raises(TimeoutError):
        module.read_top(CID, deadline=10.5)


class PipeEndpoint:
    """OS readiness at a high FD without any real or simulated socket access."""

    def __init__(self, fd):
        self.fd = fd

    def fileno(self):
        return self.fd

    def recv(self, maximum):
        return os.read(self.fd, maximum)

    def send(self, payload):
        return os.write(self.fd, payload)


@contextmanager
def high_fd_pipe():
    read_fd, write_fd = os.pipe()
    high_read = high_write = None
    try:
        high_read = fcntl.fcntl(read_fd, fcntl.F_DUPFD_CLOEXEC, 2048)
        high_write = fcntl.fcntl(write_fd, fcntl.F_DUPFD_CLOEXEC, 2048)
        assert high_read >= 2048 and high_write >= 2048
        os.set_blocking(high_read, False)
        os.set_blocking(high_write, False)
        yield high_read, high_write
    finally:
        for fd in (high_read, high_write, read_fd, write_fd):
            if fd is not None:
                os.close(fd)


def test_high_fd_receive_uses_os_selector_without_select_limit():
    with high_fd_pipe() as (read_fd, write_fd):
        os.write(write_fd, b"fresh")
        stream = module._Stream(PipeEndpoint(read_fd), time.monotonic() + 1)
        assert stream.receive(5) == b"fresh"


def test_high_fd_send_uses_os_selector_without_select_limit():
    with high_fd_pipe() as (read_fd, write_fd):
        stream = module._Stream(PipeEndpoint(write_fd), time.monotonic() + 1)
        stream.send(b"fresh")
        assert os.read(read_fd, 5) == b"fresh"


def test_high_fd_read_timeout_respects_original_deadline():
    with high_fd_pipe() as (read_fd, _):
        started = time.monotonic()
        stream = module._Stream(PipeEndpoint(read_fd), started + 0.03)
        with pytest.raises(TimeoutError):
            stream.receive(1)
        assert time.monotonic() - started < 0.5


def test_high_fd_send_timeout_respects_original_deadline():
    with high_fd_pipe() as (_, write_fd):
        for _ in range(128):
            try:
                os.write(write_fd, b"x" * 65536)
            except BlockingIOError:
                break
        else:
            pytest.fail("pipe did not reach its bounded capacity")
        started = time.monotonic()
        stream = module._Stream(PipeEndpoint(write_fd), started + 0.03)
        with pytest.raises(TimeoutError):
            stream.send(b"x")
        assert time.monotonic() - started < 0.5


@pytest.mark.parametrize(
    "event", [module.selectors.EVENT_READ, module.selectors.EVENT_WRITE]
)
def test_memory_selector_timeout_closes_and_never_restarts_budget(monkeypatch, event):
    calls = []

    class TimeoutSelector:
        def register(self, connection, requested):
            assert requested == event
            calls.append("register")

        def select(self, timeout):
            assert timeout == 0.25
            calls.append("wait")
            return []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append("close")

    monkeypatch.setattr(module.time, "monotonic", lambda: 10)
    monkeypatch.setattr(module.selectors, "DefaultSelector", TimeoutSelector)
    stream = module._Stream(MemorySocket(b""), 10.25)
    with pytest.raises(TimeoutError):
        stream._wait(event)
    assert calls == ["register", "wait", "close"]


@pytest.mark.parametrize("operation", ["connect", "send"])
@pytest.mark.parametrize("error_type", [TimeoutError, OSError])
def test_memory_connect_send_error_closes_without_retry(
    monkeypatch, memory_wire, operation, error_type
):
    channel = MemorySocket(response())
    failing = Mock(side_effect=error_type("synthetic transport failure"))
    setattr(channel, operation, failing)
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    with pytest.raises(error_type):
        read()
    assert factory.call_count == failing.call_count == 1
    assert channel.closed
    assert not channel.request


def test_memory_zero_byte_send_closes_without_retry(monkeypatch, memory_wire):
    channel = MemorySocket(response())
    channel.send = Mock(return_value=0)
    channel.recv = Mock(
        side_effect=AssertionError("no response read after failed send")
    )
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    with pytest.raises(module.DaemonTopError):
        read()
    assert factory.call_count == channel.send.call_count == 1
    channel.recv.assert_not_called()
    assert channel.closed


@pytest.mark.parametrize("operation", ["send", "recv"])
def test_memory_blocking_io_waits_again_without_replaying_request(
    monkeypatch, memory_wire, operation
):
    channel = MemorySocket(response(), fragment=8192)
    original = getattr(channel, operation)
    blocked = [False]

    def once(payload):
        if not blocked[0]:
            blocked[0] = True
            raise BlockingIOError()
        return original(payload)

    setattr(channel, operation, once)
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    assert parse_top(read())[100] == (0, 1, 4026532777)
    assert bytes(channel.request).count(b"GET ") == 1
    assert factory.call_count == 1
    assert channel.closed and blocked[0]


@pytest.mark.parametrize("operation", ["send", "recv"])
def test_memory_repeated_blocking_io_cannot_extend_deadline(
    monkeypatch, memory_wire, operation
):
    channel = MemorySocket(response(), fragment=8192)
    setattr(channel, operation, Mock(side_effect=BlockingIOError()))
    factory = Mock(return_value=channel)
    monkeypatch.setattr(module.socket, "socket", factory)
    clock = iter([10 + i / 100 for i in range(1000)])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        module.read_top(CID, deadline=10.5)
    assert factory.call_count == 1
    assert bytes(channel.request).count(b"GET ") <= 1
    assert channel.closed


@pytest.mark.parametrize("count", [1024, 1025])
def test_memory_exact_chunk_count_boundary_with_otherwise_valid_json(
    memory_wire, count
):
    body = BODY + b" " * (count - len(BODY))
    assert json.loads(body) == DOCUMENT
    framed = b"".join(b"1\r\n" + bytes([c]) + b"\r\n" for c in body) + b"0\r\n\r\n"
    stream = memory_wire(response(framed, headers=[b"Transfer-Encoding: chunked"]))
    if count == 1024:
        assert parse_top(module._cli_text(module._body(stream)))[100] == (
            0,
            1,
            4026532777,
        )
    else:
        with pytest.raises(module.DaemonTopError):
            module._body(stream)


@pytest.mark.parametrize(
    "field,value", [(0, "0"), (0, "-1"), (1, "root"), (3, "0"), (4, "x" * 65)]
)
def test_memory_projection_retains_both_identity_parsers(field, value):
    document = json.loads(BODY)
    document["Processes"][0][field] = value
    raw = module._cli_text(json.dumps(document).encode())
    for parser in (identity.parse_top, rd_identity.parse_top):
        with pytest.raises(ProcessError):
            parser(raw)
