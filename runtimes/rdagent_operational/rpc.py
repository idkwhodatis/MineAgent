"""Bounded RD-only Unix RPC transport; authentication and transactions stay host-side.

A connected socket is not authority. The host calls the supplied live peer verifier
for EVERY request before invoking its fixed handler. This module neither issues a
process nor opens a provider. No retries after an uncertain socket outcome: the
controller must stop and the host reconciles its retained operation records.
"""

import json
import math
import socket
import struct
import time

SCHEMA = "mineagent.rdagent-controller-rpc.v1"
MAX_BYTES = 1_048_576
METHODS = frozenset(
    {
        "check_control",
        "begin_step",
        "begin_exchange",
        "end_exchange",
        "submit_chat",
        "submit_embedding",
        "semantic_retry",
        "publish_source",
        "reject_source",
        "begin_evaluation",
        "execute_or_reuse",
        "finish_evaluation",
        "generation_boundary",
    }
)


class RPCError(RuntimeError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RPCError("duplicate_key")
        result[key] = value
    return result


def _shape(value, depth=0):
    if depth > 20:
        raise RPCError("depth")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _shape(item, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _shape(item, depth + 1)
        return
    raise RPCError("primitive_only")


def encode(value):
    _shape(value)
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if not 1 <= len(raw) <= MAX_BYTES:
        raise RPCError("frame_size")
    return struct.pack("!I", len(raw)) + raw


def _timeout(channel, deadline):
    remaining = deadline - time.monotonic()
    if not 0 < remaining <= 3600:
        raise RPCError("deadline")
    channel.settimeout(remaining)


def _read(channel, count, deadline):
    chunks = bytearray()
    while len(chunks) < count:
        _timeout(channel, deadline)
        chunk = channel.recv(count - len(chunks))
        if not chunk:
            raise RPCError("incomplete_frame")
        chunks.extend(chunk)
    return bytes(chunks)


def receive(channel, deadline):
    try:
        size = struct.unpack("!I", _read(channel, 4, deadline))[0]
        if not 1 <= size <= MAX_BYTES:
            raise RPCError("frame_size")
        raw = _read(channel, size, deadline)
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(RPCError("nonfinite")),
        )
        _shape(value)
        if encode(value)[4:] != raw:
            raise RPCError("noncanonical_frame")
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        raise RPCError("invalid_frame") from error


def send(channel, value, deadline):
    try:
        _timeout(channel, deadline)
        channel.sendall(encode(value))
    except OSError as error:
        raise RPCError("uncertain_send") from error


class Client:
    def __init__(self, channel: socket.socket, ticket: str, *, deadline: float):
        if (
            type(ticket) is not str
            or len(ticket) != 64
            or any(c not in "0123456789abcdef" for c in ticket)
        ):
            raise RPCError("ticket")
        self.channel, self.ticket, self.deadline = channel, ticket, deadline
        self.sequence = 0
        self.failed = False

    def call(self, method, body):
        if (
            self.failed
            or type(method) is not str
            or method not in METHODS
            or type(body) is not dict
            or self.sequence >= 10000
        ):
            raise RPCError("closed_or_invalid_method")
        sequence = self.sequence
        try:
            send(
                self.channel,
                {
                    "schema_version": SCHEMA,
                    "sequence": sequence,
                    "ticket": self.ticket,
                    "method": method,
                    "body": body,
                },
                self.deadline,
            )
            response = receive(self.channel, self.deadline)
            if (
                type(response) is not dict
                or set(response) != {"schema_version", "sequence", "result"}
                or response["schema_version"] != SCHEMA
                or type(response["sequence"]) is not int
                or response["sequence"] != sequence
            ):
                raise RPCError("response_binding")
            self.sequence += 1
            return response["result"]
        except BaseException:
            self.failed = True
            raise


def serve_one(channel, *, sequence, deadline, authenticate, handle):
    """Return next sequence after one verified request; errors terminate the channel.

    `authenticate(socket,ticket)` must reread exact persisted process identity and
    current fence. `handle(authority,method,body)` owns its actual fenced DB work;
    no reusable authority or arbitrary method lookup is serialized in responses.
    """
    request = receive(channel, deadline)
    if (
        type(sequence) is not int
        or not 0 <= sequence < 10000
        or type(request) is not dict
        or set(request) != {"schema_version", "sequence", "ticket", "method", "body"}
        or request["schema_version"] != SCHEMA
        or type(request["sequence"]) is not int
        or request["sequence"] != sequence
        or type(request["method"]) is not str
        or request["method"] not in METHODS
        or type(request["body"]) is not dict
    ):
        raise RPCError("request_binding")
    authority = authenticate(channel, request["ticket"])
    result = handle(authority, request["method"], request["body"])
    send(
        channel,
        {"schema_version": SCHEMA, "sequence": sequence, "result": result},
        deadline,
    )
    return sequence + 1
