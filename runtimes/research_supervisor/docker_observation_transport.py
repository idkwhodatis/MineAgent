"""Fresh read-only Docker transport for trusted RD/OE controller observations.

Only the existing fixed Unix endpoint and inspect/top GETs are available. No
negotiation, fallback, connection reuse, caching, launch, or authority issuance.
Callers retain their profile-specific deadline, host snapshots and peer checks.
Socket/framing restrictions and conservative inspect sizing are fail-closed
restrictions relative to the CLI; they are not universal acceptance parity.
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


def _fail(code="research_process_uncertain"):
    raise ProcessError(code) from None


class _Budget:
    def __init__(self, deadline, identity_code):
        self.deadline = deadline
        self.identity_code = identity_code
        self.end = min(deadline, time.monotonic() + 10)

    def remaining(self):
        now = time.monotonic()
        if not 0 < self.deadline - now <= 600:
            _fail(self.identity_code)
        remaining = self.end - now
        if remaining <= 0:
            _fail()
        return remaining

    def io_error(self):
        # A newly enforced original-deadline interruption has the same category
        # as the caller's original post-command deadline rejection.
        if time.monotonic() >= self.deadline:
            _fail(self.identity_code)
        _fail()


def _identity():
    info = os.lstat(ENDPOINT)
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007:
        _fail()
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


def _invalid_constant(_):
    raise ValueError()


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
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
        self.connection.settimeout(self.deadline.remaining())
        limit = min(len(target), MAX_WIRE + 1 - self.count)
        if limit <= 0:
            _fail()
        count = self.connection.recv_into(memoryview(target)[:limit])
        self.count += count
        if self.count > MAX_WIRE:
            _fail()
        self.deadline.remaining()
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
        if re.fullmatch(rb"HTTP/1\.1 (?:200|404) [^\r\n]*\r\n", line) is None:
            _fail()
        return "HTTP/1.1", int(line[9:12]), line[13:-2].decode("latin-1")

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
                _fail("research_process_output")
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
            _fail("research_process_output")


def _get(path, budget):
    if (
        type(path) is not str
        or re.fullmatch(
            re.escape(API)
            + r"/containers/[a-f0-9]{64}/(?:json|top\?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm)",
            path,
        )
        is None
    ):
        _fail()
    budget.remaining()
    response = None
    wrapped = None
    try:
        identity = _identity()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(budget.remaining())
            connection.connect(ENDPOINT)
            pid, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if pid <= 0 or uid != 0 or _identity() != identity:
                _fail()
            connection.settimeout(budget.remaining())
            connection.sendall(
                f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\nAccept: application/json\r\n\r\n".encode(
                    "ascii"
                )
            )
            wrapped = _ResponseSocket(connection, budget)
            response = _StrictResponse(wrapped)
            response.begin()
            if response.status not in (200, 404) or response.version != 11:
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
                    and (re.fullmatch(r"[0-9]{1,10}", length) is None)
                )
            ):
                _fail()
            if length is not None and int(length) > MAX_BODY:
                _fail("research_process_output")
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                _fail("research_process_output")
            if length is not None and len(raw) != int(length):
                _fail()
            if wrapped.reader.read(1):
                _fail()
            budget.remaining()
            if _identity() != identity:
                _fail()
            inspect = path.endswith("/json")
            try:
                value = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique,
                    parse_constant=_invalid_constant,
                )
            except (ValueError, TypeError, RecursionError):
                _fail()
            if response.status == 404:
                if (
                    inspect
                    and type(value) is dict
                    and type(value.get("message")) is str
                    and value["message"].startswith(
                        ("No such container:", "No such object:")
                    )
                ):
                    budget.remaining()
                    return None
                _fail()
            if value is None and inspect:
                _inspect_render_bound(raw)
                budget.remaining()
                return None
            if value is None and not inspect:
                value = {}
            if type(value) is not dict:
                _fail()
            if inspect:
                _inspect_render_bound(raw)
            budget.remaining()
            return value
    except OSError:
        budget.io_error()
    except (
        ValueError,
        TypeError,
        RecursionError,
        struct.error,
        http.client.HTTPException,
    ):
        _fail()
    finally:
        if response is not None:
            response.close()
        if wrapped is not None:
            wrapped.reader.close()


class DockerObservationTransport:
    """Only the original observer's inspect/top interface, not a supervisor."""

    def __init__(self, image_id, *, deadline, identity_code):
        if (
            type(image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        ):
            _fail("research_process_image")
        if type(identity_code) is not str or identity_code not in (
            "rd_controller_daemon_identity",
            "oe_controller_daemon_identity",
        ):
            _fail()
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            _fail(identity_code)
        self.deadline = deadline
        self.identity_code = identity_code

    def _budget(self):
        return _Budget(self.deadline, self.identity_code)

    def _inspect(self, container_id):
        if (
            type(container_id) is not str
            or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        ):
            _fail(self.identity_code)
        return _get(f"{API}/containers/{container_id}/json", self._budget())

    def _command(self, args):
        if (
            type(args) is not list
            or len(args) != 4
            or any(type(arg) is not str for arg in args)
            or args[0] != "top"
            or args[2:] != ["-eo", TOP_FIELDS]
        ):
            _fail(self.identity_code)
        container_id = args[1]
        if re.fullmatch(r"[a-f0-9]{64}", container_id) is None:
            _fail(self.identity_code)
        budget = self._budget()
        value = _get(
            f"{API}/containers/{container_id}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm",
            budget,
        )
        if type(value) is not dict:
            _fail()
        # Docker CLI decodes this into a Go struct: unknown fields are ignored,
        # absent/null slices default empty, and null string cells default empty.
        # Keep rejecting duplicate case-folded fields rather than choosing one.
        fields = {}
        for key, item in value.items():
            if key.isascii() and key.lower() in ("titles", "processes"):
                normalized = key.lower()
                if normalized in fields:
                    _fail()
                fields[normalized] = item
        titles = fields.get("titles")
        if titles is None:
            titles = []
        if type(titles) is not list or any(
            cell is not None and type(cell) is not str for cell in titles
        ):
            _fail()
        titles = ["" if cell is None else cell for cell in titles]
        rows = fields.get("processes")
        if rows is None:
            rows = []
        if type(rows) is not list:
            _fail()
        normalized_rows = []
        for row in rows:
            if row is None:
                row = []
            if type(row) is not list or any(
                cell is not None and type(cell) is not str for cell in row
            ):
                _fail()
            row = ["" if cell is None else cell for cell in row]
            normalized_rows.append(row)
        rows = normalized_rows
        if titles != ["PID", "UID", "PPID", "PIDNS", "COMMAND"]:
            _fail(self.identity_code)
        for row in rows:
            if len(row) != 5 or any(
                not cell or any(c.isspace() for c in cell) for cell in row
            ):
                _fail(self.identity_code)
        all_rows = [titles, *rows]
        widths = [max(20, max(len(row[i]) for row in all_rows) + 3) for i in range(4)]
        rendered_size = sum(sum(widths) + len(row[4]) + 1 for row in all_rows)
        if rendered_size > MAX_BODY:
            _fail("research_process_output")
        if not 1 <= len(rows) <= 64:
            _fail(self.identity_code)
        try:
            raw = (
                "\n".join(
                    "".join(row[i].ljust(widths[i]) for i in range(4)) + row[4]
                    for row in all_rows
                )
                + "\n"
            ).encode("ascii")
        except UnicodeError:
            _fail(self.identity_code)
        budget.remaining()
        # The unchanged RD/OE parser owns its original16KiB/numeric/row checks.
        return 0, raw
