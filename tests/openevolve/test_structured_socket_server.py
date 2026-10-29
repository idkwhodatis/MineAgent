"""Actual local socket framing and original-deadline bounds (no Docker grant)."""

import socket
import struct
import time

import pytest

from backend.app import oe_structured_socket_server as server
from packages.research_runtime.process_contracts import ProcessError


def test_actual_socket_frame_and_reply():
    left, right = socket.socketpair()
    with left, right:
        right.sendall(struct.pack("!I", 5) + b"hello")
        assert server.read_frame(left, time.monotonic() + 1, 5) == b"hello"
        server.write_frame(left, b"reply", time.monotonic() + 1)
        assert right.recv(9) == struct.pack("!I", 5) + b"reply"


@pytest.mark.parametrize("size", [0, 6, 4294967295])
def test_invalid_length_rejected_before_payload_read(size):
    left, right = socket.socketpair()
    with left, right:
        right.sendall(struct.pack("!I", size))
        with pytest.raises(ProcessError, match="socket_bound"):
            server.read_frame(left, time.monotonic() + 1, 5)


@pytest.mark.parametrize("raw", [b"", b"\x00", struct.pack("!I", 5) + b"ab"])
def test_partial_or_missing_frame_never_success(raw):
    left, right = socket.socketpair()
    with left, right:
        right.sendall(raw)
        right.shutdown(socket.SHUT_WR)
        with pytest.raises(ProcessError, match="socket_eof"):
            server.read_frame(left, time.monotonic() + 1, 5)


def test_expired_original_deadline_rejects_even_buffered_frame():
    left, right = socket.socketpair()
    with left, right:
        right.sendall(struct.pack("!I", 1) + b"x")
        with pytest.raises(ProcessError, match="socket_deadline"):
            server.read_frame(left, time.monotonic() - 1, 5)


def test_partial_frame_wait_is_bounded_by_original_deadline():
    left, right = socket.socketpair()
    with left, right:
        right.sendall(struct.pack("!I", 5) + b"x")
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            server.read_frame(left, start + 0.03, 5)
        assert time.monotonic() - start < 1


def test_no_unbounded_reply_or_nonbytes():
    left, right = socket.socketpair()
    with left, right:
        for raw in (b"", b"x" * 262145, "x"):
            with pytest.raises(ProcessError, match="reply_bound"):
                server.write_frame(left, raw, time.monotonic() + 1)


def test_caller_dto_cannot_supply_running_controller():
    with pytest.raises(ProcessError, match="socket_owner"):
        server.serve_controller(
            None, None, None, None, running=object(), ticket_key=b"x" * 32
        )
