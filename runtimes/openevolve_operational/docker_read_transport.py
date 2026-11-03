"""LOCAL candidate: bounded, read-only Docker observations; not wired by default.

No negotiation, fallback, connection reuse, batching, or environment configuration.
API v1.45 support and actual latency must be established in isolated CI. Rejected
responses never fall back to CLI because that would replace an observation.
"""

import http.client
import io
import json
import math
import os
import re
import socket
import stat
import struct
import time

from packages.research_runtime.process_contracts import ProcessError

ENDPOINT = "/var/run/docker.sock"
API = "/v1.45"
MAX_WIRE = 81920
MAX_BODY = 65536
TOP_FIELDS = "pid,uid,ppid,pidns,comm"


def _fail():
    raise ProcessError("oe_controller_daemon_identity") from None


def _remaining(deadline):
    value = deadline - time.monotonic()
    if not 0 < value <= 600:
        _fail()
    return value


def _identity():
    info = os.lstat(ENDPOINT)
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007:
        _fail()
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail()
        result[key] = value
    return result


class _BoundedReader(io.RawIOBase):
    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.count = 0

    def readable(self):
        return True

    def readinto(self, target):
        self.connection.settimeout(_remaining(self.deadline))
        limit = min(len(target), MAX_WIRE + 1 - self.count)
        if limit <= 0:
            _fail()
        count = self.connection.recv_into(memoryview(target)[:limit])
        self.count += count
        if self.count > MAX_WIRE:
            _fail()
        _remaining(self.deadline)
        return count


class _StrictLines(io.BufferedReader):
    def readline(self, size=-1):
        line = super().readline(size)
        if (
            not line.endswith(b"\r\n")
            or b"\r" in line[:-2]
            or b"\n" in line[:-2]
            or line.startswith((b" ", b"\t"))
        ):
            _fail()
        return line


class _StrictResponse(http.client.HTTPResponse):
    def _close_conn(self):
        # The outer owner closes the reader after checking EOF. HTTPResponse
        # normally closes it at Content-Length/last chunk, hiding trailing data.
        self.fp = None

    def _read_status(self):
        line = self.fp.readline(65537)
        if re.fullmatch(rb"HTTP/1\.1 200 [^\r\n]*\r\n", line) is None:
            _fail()
        return "HTTP/1.1", 200, line[13:-2].decode("latin-1")

    def _get_chunk_left(self):
        left = self.chunk_left
        if not left:
            if left is not None and self._safe_read(2) != b"\r\n":
                _fail()
            line = self.fp.readline(32)
            if re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line) is None:
                _fail()
            left = int(line, 16)
            if left > MAX_BODY:
                _fail()
            if left == 0:
                # Docker does not need response trailers for these JSON GETs.
                if self.fp.readline(3) != b"\r\n":
                    _fail()
                self._close_conn()
                left = None
            self.chunk_left = left
        return left


class _ResponseSocket:
    def __init__(self, connection, deadline):
        self.reader = _StrictLines(_BoundedReader(connection, deadline))

    def makefile(self, mode):
        if mode != "rb":
            _fail()
        return self.reader


def _inspect_render_bound(raw):
    """Conservative upper bound on CLI json.Indent([raw], four spaces).

    Retain all input bytes (including whitespace and exact escape/number tokens)
    and overcharge every structural token for newline/indentation. This can
    reject a response the CLI accepted, but cannot admit oversized CLI output.
    """
    total, depth, quoted, escaped = len(raw) + 16, 1, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (123, 91):
            depth += 1
            total += 1 + 4 * (depth + 1)
        elif byte in (125, 93):
            total += 1 + 4 * (depth + 1)
            depth -= 1
        elif byte == 44:
            total += 1 + 4 * (depth + 1)
        elif byte == 58:
            total += 1
        if total > MAX_BODY:
            _fail()


def _get(path, deadline):
    if (
        re.fullmatch(
            re.escape(API)
            + r"/containers/[a-f0-9]{64}/(?:json|top\?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm)",
            path,
        )
        is None
    ):
        _fail()
    _remaining(deadline)
    end = min(deadline, time.monotonic() + 10)
    response = None
    wrapped = None
    try:
        identity = _identity()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_remaining(end))
            connection.connect(ENDPOINT)
            pid, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if pid <= 0 or uid != 0 or _identity() != identity:
                _fail()
            connection.settimeout(_remaining(end))
            connection.sendall(
                f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\nAccept: application/json\r\n\r\n".encode(
                    "ascii"
                )
            )
            wrapped = _ResponseSocket(connection, end)
            response = _StrictResponse(wrapped)
            response.begin()
            if response.status != 200 or response.version != 11:
                _fail()
            headers = response.headers
            if headers.defects:
                _fail()
            for name in (
                "Content-Length",
                "Transfer-Encoding",
                "Content-Encoding",
                "Content-Type",
            ):
                if len(headers.get_all(name, [])) > 1:
                    _fail()
            length = headers.get("Content-Length")
            transfer = headers.get("Transfer-Encoding")
            if (
                headers.get("Content-Encoding") is not None
                or headers.get("Content-Type", "").lower() != "application/json"
                or transfer not in (None, "chunked")
                or (length is not None and transfer is not None)
                or (length is None and transfer is None)
                or (
                    length is not None
                    and (
                        re.fullmatch(r"[0-9]{1,5}", length) is None
                        or int(length) > MAX_BODY
                    )
                )
            ):
                _fail()
            raw = response.read(MAX_BODY + 1)
            if not 0 < len(raw) <= MAX_BODY or (
                length is not None and len(raw) != int(length)
            ):
                _fail()
            if wrapped.reader.read(1):
                _fail()
            _remaining(end)
            if _identity() != identity:
                _fail()
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique,
                parse_constant=lambda _: _fail(),
            )
            if type(value) is not dict:
                _fail()
            if path.endswith("/json"):
                _inspect_render_bound(raw)
            _remaining(end)
            return value
    except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException):
        _fail()
    finally:
        if response is not None:
            response.close()
        if wrapped is not None:
            wrapped.reader.close()


class DockerReadTransport:
    """Small adapter matching only the two observer operations, never a supervisor."""

    def __init__(self, image_id, *, deadline):
        if (
            type(image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
        ):
            _fail()
        self.deadline = deadline

    def _inspect(self, container_id):
        if (
            type(container_id) is not str
            or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        ):
            _fail()
        return _get(f"{API}/containers/{container_id}/json", self.deadline)

    def _command(self, args):
        if (
            type(args) is not list
            or len(args) != 4
            or any(type(arg) is not str for arg in args)
            or args[0] != "top"
            or args[2:] != ["-eo", TOP_FIELDS]
        ):
            _fail()
        container_id = args[1]
        if (
            type(container_id) is not str
            or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        ):
            _fail()
        value = _get(
            f"{API}/containers/{container_id}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm",
            self.deadline,
        )
        if set(value) != {"Titles", "Processes"} or value["Titles"] != [
            "PID",
            "UID",
            "PPID",
            "PIDNS",
            "COMMAND",
        ]:
            _fail()
        rows = value["Processes"]
        if type(rows) is not list or not 1 <= len(rows) <= 64:
            _fail()
        for row in rows:
            if (
                type(row) is not list
                or len(row) != 5
                or any(
                    type(cell) is not str or not cell or any(c.isspace() for c in cell)
                    for cell in row
                )
            ):
                _fail()
        try:
            all_rows = [value["Titles"], *rows]
            widths = [
                max(20, max(len(row[i]) for row in all_rows) + 3) for i in range(4)
            ]
            raw = (
                "\n".join(
                    "".join(row[i].ljust(widths[i]) for i in range(4)) + row[4]
                    for row in all_rows
                )
                + "\n"
            ).encode("ascii")
        except UnicodeError:
            _fail()
        from runtimes.openevolve_operational.daemon_identity import parse_top

        parse_top(raw)
        _remaining(self.deadline)
        return 0, raw
