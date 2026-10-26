"""Disabled numeric pre-start Docker attach transport; no execution authority.

Transparent copy of runtimes/hostile_deadline/attach_stream.py at
d36cce627bebd972b543df3155646942034dd72a (SHA256
b356cf0bbd965c9025a6804ef01f16cddcfef5faa36752dd2dbf2266955d1bc0).
Only the fixed aggregate cap changes: 17 MiB accommodates the separate numeric
16 MiB output plus bounded metadata. The historical 5 MiB proof is unchanged.
Independent numeric Docker acceptance remains required.

Protocol: Moby v28.3.2 container_routes.go postContainersAttach, API >=1.42.
Only HTTP 101 multiplexed upgrade is accepted; TTY/raw streams are rejected.
No launcher/admission authority is provided by this byte transport.
"""

from __future__ import annotations

import math
import re
import select
import socket
import time
from typing import Self

MAX_HEADER_BYTES = 8192
MAX_FRAME_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 17 * 1024 * 1024
MAX_FRAMES = 8192
SOCKET_PATH = "/var/run/docker.sock"


class AttachProtocolError(ValueError):
    """Closed framing/upgrade violation; raw peer bytes are not exposed in errors."""


def _remaining(deadline: float) -> float:
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("invalid attach deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("original attach deadline expired")
    return remaining


def parse_upgrade(raw: bytes) -> None:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_HEADER_BYTES:
        raise AttachProtocolError("invalid attach header bound")
    if not raw.endswith(b"\r\n\r\n"):
        raise AttachProtocolError("incomplete attach upgrade")
    try:
        lines = raw[:-4].decode("ascii").split("\r\n")
    except UnicodeError as error:
        raise AttachProtocolError("invalid attach header encoding") from error
    if not re.fullmatch(r"HTTP/1\.1 101 [A-Za-z ]+", lines[0]):
        raise AttachProtocolError("attach upgrade rejected")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line or line[:1].isspace():
            raise AttachProtocolError("invalid attach header")
        name, value = line.split(":", 1)
        name = name.lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in headers:
            raise AttachProtocolError("ambiguous attach header")
        headers[name] = value.strip()
    if (
        headers.get("connection", "").lower() != "upgrade"
        or headers.get("upgrade", "").lower() != "tcp"
        or headers.get("content-type") != "application/vnd.docker.multiplexed-stream"
        or "transfer-encoding" in headers
        or "content-length" in headers
    ):
        raise AttachProtocolError("not an exact multiplexed attach upgrade")


class AttachStream:
    """One strict capped stream; terminal errors poison and close its socket."""

    def __init__(self, connection: socket.socket, initial: bytes = b"") -> None:
        self._socket = connection
        self._socket.setblocking(False)
        self._buffer = bytearray(initial)
        self._total = 0
        self._frames = 0
        self._closed = False
        self._eof = False
        if len(initial) > MAX_FRAME_BYTES + 8:
            self.close()
            raise AttachProtocolError("oversized initial attach bytes")

    @classmethod
    def connect(cls, cid: str, *, deadline: float) -> AttachStream:
        if type(cid) is not str or re.fullmatch(r"[0-9a-f]{64}", cid) is None:
            raise ValueError("exact container identity required")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(_remaining(deadline))
            connection.connect(SOCKET_PATH)
            stream = cls(connection)
            request = (
                f"POST /v1.47/containers/{cid}/attach?stream=1&stdout=1&stderr=1&stdin=0&logs=0 HTTP/1.1\r\n"
                "Host: localhost\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: 0\r\n\r\n"
            ).encode("ascii")
            stream._send(request, deadline)
            while b"\r\n\r\n" not in stream._buffer:
                if len(stream._buffer) >= MAX_HEADER_BYTES:
                    raise AttachProtocolError("oversized attach headers")
                chunk = stream._receive(
                    deadline, MAX_HEADER_BYTES - len(stream._buffer)
                )
                if not chunk:
                    raise AttachProtocolError("truncated attach upgrade")
                stream._buffer.extend(chunk)
            boundary = stream._buffer.index(b"\r\n\r\n") + 4
            parse_upgrade(bytes(stream._buffer[:boundary]))
            del stream._buffer[:boundary]
            return stream
        except BaseException:
            connection.close()
            raise

    def _send(self, payload: bytes, deadline: float) -> None:
        offset = 0
        while offset < len(payload):
            _, ready, _ = select.select([], [self._socket], [], _remaining(deadline))
            if not ready:
                raise TimeoutError("attach write deadline expired")
            try:
                written = self._socket.send(payload[offset:])
            except BlockingIOError:
                continue
            if written == 0:
                raise AttachProtocolError("attach socket closed during request")
            offset += written

    def _receive(self, deadline: float, limit: int) -> bytes:
        while True:
            ready, _, _ = select.select([self._socket], [], [], _remaining(deadline))
            if not ready:
                raise TimeoutError("attach read deadline expired")
            try:
                return self._socket.recv(min(limit, 8192))
            except BlockingIOError:
                continue

    def _fill(self, length: int, deadline: float) -> bool:
        while len(self._buffer) < length:
            chunk = self._receive(deadline, length - len(self._buffer))
            if not chunk:
                return False
            self._buffer.extend(chunk)
        return True

    def read_frame(self, *, deadline: float) -> tuple[int, bytes] | None:
        if self._closed:
            if self._eof:
                return None
            raise AttachProtocolError("attach stream is closed")
        try:
            _remaining(deadline)
            if not self._fill(8, deadline):
                if self._buffer:
                    raise AttachProtocolError("truncated attach frame header")
                self._eof = True
                self.close()
                return None
            header = bytes(self._buffer[:8])
            stream = header[0]
            length = int.from_bytes(header[4:], "big")
            if stream not in (1, 2) or header[1:4] != b"\0\0\0":
                raise AttachProtocolError("invalid attach frame type")
            if not 0 < length <= MAX_FRAME_BYTES:
                raise AttachProtocolError("invalid attach frame size")
            if self._total + length > MAX_TOTAL_BYTES or self._frames >= MAX_FRAMES:
                raise AttachProtocolError("aggregate attach cap exceeded")
            if not self._fill(8 + length, deadline):
                raise AttachProtocolError("truncated attach frame payload")
            payload = bytes(self._buffer[8 : 8 + length])
            del self._buffer[: 8 + length]
            self._frames += 1
            self._total += length
            return stream, payload
        except BaseException:
            self.close()
            raise

    def collect_until_eof(self, *, deadline: float) -> dict[int, bytes]:
        output = {1: bytearray(), 2: bytearray()}
        while (frame := self.read_frame(deadline=deadline)) is not None:
            stream, payload = frame
            output[stream].extend(payload)
        return {stream: bytes(payload) for stream, payload in output.items()}

    def close(self) -> None:
        self._closed = True
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
