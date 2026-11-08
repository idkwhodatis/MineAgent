"""Bounded read-only Docker top transport for the host's fresh RD and OE observers.

The endpoint is the same /var/run/docker.sock selected by DockerSupervisor,
never an inherited context, TLS/SSH host, proxy, or rootless socket discovery.
Each call connects anew and sends one GET. It never retries or falls back.

Moby v28.3.2 client/container_top.go joins the fixed ps arguments with a space.
api/server/router/container/container_routes.go getContainersTop forwards them
unchanged, without an API-version branch, to daemon/top_unix.go ContainerTop.
The unversioned route therefore avoids a new client/server version restriction.
Inspect, launch, stop, and the ordinary OE pair observer still use the existing CLI.

Docker CLI v28.3.2 cli/command/container/top.go uses tabwriter.NewWriter with
(20, 1, 3, ' ', 0). For the accepted five ASCII fields, _cli_text reproduces
those bytes, not a shorter normalized representation. The caller still runs
the original parse_top and its 16 KiB bound. A wire cap is separate: Go JSON
may escape each '<', '>', or '&' in a valid command as six bytes.
These source equivalences are not a real-daemon containment acceptance claim.

This bounded reader supports the reviewed Moby response, not generic HTTP
compatibility: HTTP/1.0, MIME parameters, even benign duplicate headers, chunk
extensions/trailers, extra JSON fields, and whitespace-bearing cells are
intentionally rejected. Broader response acceptance requires demonstrated
official-daemon compatibility evidence, not a fallback to the CLI.
"""

import json
import math
import re
import selectors
import socket
import time

SOCKET_PATH = "/var/run/docker.sock"
MAX_HEADER_BYTES = 8192
MAX_JSON_BYTES = 65536
MAX_CHUNKS = 1024
MAX_TOP_BYTES = 16384
MAX_PROCESSES = 64
TITLES = ["PID", "UID", "PPID", "PIDNS", "COMMAND"]
PS_QUERY = "ps_args=-eo+pid%2Cuid%2Cppid%2Cpidns%2Ccomm"


class DaemonTopError(ValueError):
    """Closed protocol error; no raw daemon content is exposed."""


def _fail():
    raise DaemonTopError("host_daemon_top")


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("host_daemon_top_deadline")
    return remaining


class _Stream:
    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.buffer = bytearray()

    def _wait(self, event):
        # select.select rejects otherwise valid socket FDs >= FD_SETSIZE.
        # Match the existing CLI reader's scalable DefaultSelector mechanism.
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, event)
            ready = selector.select(_remaining(self.deadline))
            _remaining(self.deadline)
            if not any(mask & event for _, mask in ready):
                raise TimeoutError("host_daemon_top_deadline")

    def receive(self, limit):
        while True:
            self._wait(selectors.EVENT_READ)
            try:
                result = self.connection.recv(min(8192, limit))
                _remaining(self.deadline)
                return result
            except BlockingIOError:
                continue

    def send(self, request):
        offset = 0
        while offset < len(request):
            self._wait(selectors.EVENT_WRITE)
            try:
                count = self.connection.send(request[offset:])
            except BlockingIOError:
                continue
            if count == 0:
                _fail()
            offset += count
        _remaining(self.deadline)

    def until(self, delimiter, maximum):
        while True:
            index = self.buffer.find(delimiter)
            if index >= 0:
                end = index + len(delimiter)
                if end > maximum:
                    _fail()
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if len(self.buffer) >= maximum:
                _fail()
            chunk = self.receive(maximum - len(self.buffer))
            if not chunk:
                _fail()
            self.buffer.extend(chunk)

    def take(self, length):
        while len(self.buffer) < length:
            chunk = self.receive(length - len(self.buffer))
            if not chunk:
                _fail()
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def eof(self):
        # Connection: close is requested. A valid prefix is never enough;
        # extra responses, unframed suffixes, and missing EOF fail closed.
        if self.buffer or self.receive(1):
            _fail()


def _headers(raw):
    try:
        lines = raw[:-4].decode("ascii").split("\r\n")
    except UnicodeError:
        _fail()
    if not re.fullmatch(r"HTTP/1\.1 200 [\x20-\x7e]*", lines[0]):
        _fail()
    headers = {}
    for line in lines[1:]:
        if ":" not in line or line[:1].isspace():
            _fail()
        name, value = line.split(":", 1)
        name = name.lower()
        if (
            re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", name) is None
            or name in headers
            or any(ord(c) < 32 and c != "\t" or ord(c) > 126 for c in value)
        ):
            _fail()
        headers[name] = value.strip(" \t")
    if (
        headers.get("content-type") != "application/json"
        or headers.get("content-encoding", "identity").lower() != "identity"
        or headers.get("connection", "close").lower() != "close"
        or "trailer" in headers
        or "upgrade" in headers
    ):
        _fail()
    return headers


def _body(stream):
    headers = _headers(stream.until(b"\r\n\r\n", MAX_HEADER_BYTES))
    length, transfer = headers.get("content-length"), headers.get("transfer-encoding")
    if length is not None and transfer is not None:
        _fail()
    if length is not None:
        if re.fullmatch(r"[0-9]{1,10}", length) is None:
            _fail()
        size = int(length)
        if not 0 < size <= MAX_JSON_BYTES:
            _fail()
        body = stream.take(size)
    elif transfer is not None:
        if transfer.lower() != "chunked":
            _fail()
        data = bytearray()
        for _ in range(MAX_CHUNKS + 1):
            line = stream.until(b"\r\n", 18)
            if re.fullmatch(rb"[0-9a-fA-F]{1,16}\r\n", line) is None:
                _fail()
            size = int(line[:-2], 16)
            if size == 0:
                if stream.take(2) != b"\r\n":
                    _fail()
                break
            if len(data) + size > MAX_JSON_BYTES or _ == MAX_CHUNKS:
                _fail()
            data.extend(stream.take(size))
            if stream.take(2) != b"\r\n":
                _fail()
        body = bytes(data)
    else:
        # An HTTP/1.1 Connection: close response can be EOF-delimited.
        # No implicit persistent-connection EOF framing is accepted.
        if headers.get("connection", "").lower() != "close":
            _fail()
        data = bytearray(stream.buffer)
        stream.buffer.clear()
        while True:
            if len(data) > MAX_JSON_BYTES:
                _fail()
            chunk = stream.receive(MAX_JSON_BYTES + 1 - len(data))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        # All paths above are bounded by the same absolute deadline.
    stream.eof()
    return body


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail()
        result[key] = value
    return result


def _cli_text(raw):
    """Return the existing CLI's padded ASCII representation, never identities."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_JSON_BYTES:
        _fail()
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        _fail()
    if (
        type(document) is not dict
        or set(document) != {"Titles", "Processes"}
        or document["Titles"] != TITLES
        or type(document["Processes"]) is not list
        or not 1 <= len(document["Processes"]) <= MAX_PROCESSES
    ):
        _fail()
    rows = [TITLES, *document["Processes"]]
    for row in rows:
        if type(row) is not list or len(row) != 5:
            _fail()
        for column, field in enumerate(row):
            if (
                type(field) is not str
                or not field
                or any(not 33 <= ord(c) <= 126 for c in field)
            ):
                # Closed shape diagnostics only, never field/row contents.
                # Mixed shapes prefer non-ASCII, then controls, then spaces.
                if type(field) is not str:
                    category = "type"
                elif not field:
                    category = "empty"
                elif any(ord(c) > 127 for c in field):
                    category = "non_ascii"
                elif any(ord(c) < 32 or ord(c) == 127 for c in field):
                    category = "control"
                elif column == 4 and field.endswith(" <defunct>"):
                    # procps spelling only, not proof of process state.
                    category = "defunct_suffix"
                else:
                    category = "ascii_space"
                raise DaemonTopError(f"host_daemon_top_field:{column}:{category}")
    # All cells are now single ASCII tokens, so tabwriter's rune display width
    # is byte length. The last column has no trailing tab and receives no pad.
    widths = [max(20, max(len(row[i]) + 3 for row in rows)) for i in range(4)]
    size = sum(sum(widths) + len(row[4]) + 1 for row in rows)
    if size > MAX_TOP_BYTES:
        _fail()
    return "".join(
        "".join(row[i].ljust(widths[i]) for i in range(4)) + row[4] + "\n"
        for row in rows
    ).encode("ascii")


def read_top(container_id, *, deadline):
    """One bounded fresh observation; the caller retains all kernel brackets."""
    if (
        type(container_id) is not str
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
    ):
        _fail()
    deadline = min(deadline, time.monotonic() + 10)
    _remaining(deadline)
    request = (
        f"GET /containers/{container_id}/top?{PS_QUERY} HTTP/1.1\r\n"
        "Host: localhost\r\nAccept: application/json\r\n"
        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining(deadline))
        connection.connect(SOCKET_PATH)
        connection.setblocking(False)
        stream = _Stream(connection, deadline)
        stream.send(request)
        result = _cli_text(_body(stream))
        _remaining(deadline)
        return result
