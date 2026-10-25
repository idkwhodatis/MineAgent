"""Fixed, bounded observer IPC client. Host authorization is independently required."""

import hashlib
import json
import os
import socket
import stat
import struct
from pathlib import Path
from uuid import UUID

from .observer import SearchControlAbort

BOOTSTRAP = Path("/bootstrap/bootstrap.json")
TICKET = Path("/ipc/ticket.json")
SOCKET = "/ipc/events.sock"
MAX_MESSAGE = 1024 * 1024
EVENTS = frozenset(
    {
        "iteration_intent",
        "worker_started",
        "worker_outcome",
        "proposal_response",
        "proposal_source",
        "candidate_intent",
        "candidate_outcome",
        "seed_source",
        "execute_evaluation",
        "evaluation_complete",
        "rendered_prompt",
        "terminal_applied",
        "checkpoint_prepare",
        "checkpoint_publish",
        "checkpoint_load_prepare",
        "checkpoint_load_complete",
    }
)


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate IPC key")
        result[key] = value
    return result


def read_record(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("IPC record must be a regular file")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError("IPC record exceeds limit")
        return json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    finally:
        os.close(fd)


def bootstrap():
    value = read_record(BOOTSTRAP, 262144)
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "admission_id",
            "epoch_id",
            "spec",
            "runtime_profile_sha256",
            "config_sha256",
            "adapter_patch_sha256",
        }
        or value["schema_version"] != "mineagent.openevolve-bootstrap.v1"
    ):
        raise ValueError("Invalid fixed bootstrap")
    for key in ("admission_id", "epoch_id"):
        if str(UUID(value[key])) != value[key]:
            raise ValueError("Invalid bootstrap identity")
    for key in ("runtime_profile_sha256", "config_sha256", "adapter_patch_sha256"):
        if (
            type(value[key]) is not str
            or len(value[key]) != 64
            or any(c not in "0123456789abcdef" for c in value[key])
        ):
            raise ValueError("Invalid bootstrap digest")
    return value


def process_ticket():
    value = read_record(TICKET, 4096)
    if (
        type(value) is not dict
        or set(value) != {"ticket"}
        or type(value["ticket"]) is not str
        or len(value["ticket"]) != 64
        or any(c not in "0123456789abcdef" for c in value["ticket"])
    ):
        raise ValueError("Invalid process ticket")
    return value["ticket"]


def read_exact(peer, length):
    result = bytearray()
    while len(result) < length:
        part = peer.recv(length - len(result))
        if not part:
            raise ValueError("Interrupted observer response")
        result.extend(part)
    return bytes(result)


def host_event(kind, payload):
    if kind not in EVENTS or type(payload) is not dict:
        raise SearchControlAbort("worker_observation")
    try:
        initial = bootstrap()
        event = {
            "schema_version": "mineagent.openevolve-event.v1",
            "admission_id": initial["admission_id"],
            "epoch_id": initial["epoch_id"],
            "kind": kind,
            "payload": payload,
        }
        event_sha = hashlib.sha256(canonical(event)).hexdigest()
        wire = canonical(
            {
                "event": event,
                "event_sha256": event_sha,
                "process_ticket": process_ticket(),
            }
        )
        if len(wire) > MAX_MESSAGE:
            raise ValueError("Observer message exceeds immutable object bound")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(95)
            peer.connect(SOCKET)
            peer.sendall(struct.pack("!I", len(wire)) + wire)
            size = struct.unpack("!I", read_exact(peer, 4))[0]
            if not 0 < size <= 262144:
                raise ValueError("Observer response bound")
            reply = json.loads(
                read_exact(peer, size),
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        if (
            type(reply) is not dict
            or set(reply) != {"schema_version", "event_sha256", "receipt_id", "result"}
            or reply["schema_version"] != "mineagent.openevolve-event-result.v1"
            or reply["event_sha256"] != event_sha
            or str(UUID(reply["receipt_id"])) != reply["receipt_id"]
            or type(reply["result"]) is not dict
        ):
            raise ValueError("Observer acknowledgement mismatch")
        return reply["result"]
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        # No retries: host may already have committed intent or dispatched work.
        raise SearchControlAbort("worker_observation") from None
