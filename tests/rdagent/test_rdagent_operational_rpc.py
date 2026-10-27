"""Real local Unix transport, not authenticated Docker/DB acceptance."""

import socket
import struct
import threading
import time

import pytest

from runtimes.rdagent_operational import rpc


def test_real_unix_frames_reauthenticate_every_event():
    client, server = socket.socketpair()
    seen, failures = [], []
    deadline = time.monotonic() + 3
    marker = object()

    def authenticate(channel, ticket):
        assert channel is server and ticket == "a" * 64
        seen.append("auth")
        return marker

    def handle(authority, method, body):
        assert authority is marker
        seen.append((method, body))
        return {"ordinal": len(seen), "source": "α"}

    def serve():
        try:
            for i in range(2):
                assert (
                    rpc.serve_one(
                        server,
                        sequence=i,
                        deadline=deadline,
                        authenticate=authenticate,
                        handle=handle,
                    )
                    == i + 1
                )
        except BaseException as error:  # noqa: BLE001 - propagate thread test failure
            failures.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        caller = rpc.Client(client, "a" * 64, deadline=deadline)
        assert caller.call("check_control", {}) == {"ordinal": 2, "source": "α"}
        assert caller.call("begin_step", {"step": 0}) == {"ordinal": 4, "source": "α"}
        thread.join(timeout=3)
        assert not thread.is_alive() and not failures
        assert seen == [
            "auth",
            ("check_control", {}),
            "auth",
            ("begin_step", {"step": 0}),
        ]
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{ "x":1}', b"[] ", b"\xff"]
)
def test_invalid_json_is_rejected_before_handler(raw):
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", len(raw)) + raw)
        with pytest.raises(rpc.RPCError):
            rpc.receive(right, time.monotonic() + 1)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("size", [0, rpc.MAX_BYTES + 1, 2**32 - 1])
def test_declared_oversize_rejected_without_reading_body(size):
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", size))
        with pytest.raises(rpc.RPCError, match="frame_size"):
            rpc.receive(right, time.monotonic() + 1)
    finally:
        left.close()
        right.close()


def test_eof_and_uncertain_response_close_client_without_retry():
    left, right = socket.socketpair()
    try:
        right.close()
        caller = rpc.Client(left, "a" * 64, deadline=time.monotonic() + 1)
        with pytest.raises(rpc.RPCError):
            caller.call("submit_chat", {})
        assert caller.failed and caller.sequence == 0
        with pytest.raises(rpc.RPCError, match="closed"):
            caller.call("submit_chat", {})
    finally:
        left.close()


def test_auth_failure_never_dispatches():
    left, right = socket.socketpair()
    try:
        left.sendall(
            rpc.encode(
                {
                    "schema_version": rpc.SCHEMA,
                    "sequence": 0,
                    "ticket": "a" * 64,
                    "method": "publish_source",
                    "body": {},
                }
            )
        )

        def reject(*args):
            raise PermissionError("fixed rejection")

        def forbidden(*args):
            pytest.fail("unauthenticated handler invoked")

        with pytest.raises(PermissionError):
            rpc.serve_one(
                right,
                sequence=0,
                deadline=time.monotonic() + 1,
                authenticate=reject,
                handle=forbidden,
            )
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("sequence", [True, -1, 10000, 1])
def test_sequence_drift_rejected_before_auth(sequence):
    left, right = socket.socketpair()
    try:
        left.sendall(
            rpc.encode(
                {
                    "schema_version": rpc.SCHEMA,
                    "sequence": sequence,
                    "ticket": "a" * 64,
                    "method": "check_control",
                    "body": {},
                }
            )
        )

        def forbidden(*args):
            pytest.fail("drift reached authority")

        with pytest.raises(rpc.RPCError):
            rpc.serve_one(
                right,
                sequence=0,
                deadline=time.monotonic() + 1,
                authenticate=forbidden,
                handle=forbidden,
            )
    finally:
        left.close()
        right.close()
