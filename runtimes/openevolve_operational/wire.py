"""Closed bounded host event wire codec; receipt bytes are not authority.

Only a fixed host dispatcher may construct a reply after its transaction commit.
Peer and current fence are revalidated by that dispatcher, never by payload IDs.
"""

import hashlib
import json
import math
import socket
import struct
import time
from dataclasses import dataclass
from uuid import UUID

from packages.openevolve_adapter.checkpoint_native import canonical
from runtimes.openevolve_operational.transport import EVENTS, UPLOADS, unique

MAX_REQUEST = 1048576
MAX_REPLY = 262144


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("wire UUID")
    return UUID(value)


def _hash(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("wire hash")
    return value


def _bounded(value, depth=0):
    if depth > 24:
        raise ValueError("wire depth")
    if type(value) is dict:
        if len(value) > 10000 or any(type(key) is not str for key in value):
            raise ValueError("wire keys")
        for child in value.values():
            _bounded(child, depth + 1)
    elif type(value) is list:
        if len(value) > 10000:
            raise ValueError("wire items")
        for child in value:
            _bounded(child, depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("wire finite")
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError("wire primitive")


@dataclass(frozen=True)
class Event:
    admission_id: UUID
    epoch_id: UUID
    kind: str
    payload: dict
    event_sha256: str
    process_ticket: str
    raw_event: bytes


def parse_event(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REQUEST:
        raise ValueError("event bound")
    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )
    _bounded(value)
    if (
        type(value) is not dict
        or set(value) != {"event", "event_sha256", "process_ticket"}
        or canonical(value) != raw
    ):
        raise ValueError("canonical envelope")
    event = value["event"]
    if (
        type(event) is not dict
        or set(event)
        != {"schema_version", "admission_id", "epoch_id", "kind", "payload"}
        or type(event["payload"]) is not dict
    ):
        raise ValueError("closed event")
    kind = event["kind"]
    if type(kind) is not str or kind not in EVENTS | UPLOADS | {"worker_placement"}:
        raise ValueError("event not operational")
    schema = (
        "mineagent.openevolve-event.v1"
        if kind in EVENTS
        else "mineagent.openevolve-operational-event.v1"
    )
    if event["schema_version"] != schema:
        raise ValueError("event version")
    event_raw = canonical(event)
    digest = hashlib.sha256(event_raw).hexdigest()
    if _hash(value["event_sha256"]) != digest:
        raise ValueError("event bytes")
    return Event(
        _uuid(event["admission_id"]),
        _uuid(event["epoch_id"]),
        kind,
        event["payload"],
        digest,
        _hash(value["process_ticket"]),
        event_raw,
    )


def reply(event, receipt_id, result):
    if (
        type(event) is not Event
        or type(receipt_id) is not UUID
        or type(result) is not dict
    ):
        raise ValueError("typed durable reply")
    _bounded(result)
    raw = canonical(
        {
            "schema_version": "mineagent.openevolve-event-result.v1",
            "event_sha256": event.event_sha256,
            "receipt_id": str(receipt_id),
            "result": result,
        }
    )
    if len(raw) > MAX_REPLY:
        raise ValueError("reply bound")
    return raw


def read_frame(channel, deadline):
    if not isinstance(channel, socket.socket) or channel.family != socket.AF_UNIX:
        raise ValueError("Unix event channel")

    def read(count):
        result = bytearray()
        while len(result) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("original event deadline")
            channel.settimeout(min(95, remaining))
            part = channel.recv(count - len(result))
            if not part:
                raise ValueError("truncated event")
            result.extend(part)
        return bytes(result)

    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= MAX_REQUEST:
        raise ValueError("event framing bound")
    return parse_event(read(size))


def write_reply(channel, event, receipt_id, result, deadline):
    raw = reply(event, receipt_id, result)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("late committed reply")
    channel.settimeout(min(95, remaining))
    channel.sendall(struct.pack("!I", len(raw)) + raw)
