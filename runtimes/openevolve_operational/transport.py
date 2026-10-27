"""Fixed installed adapter transport overlay; upstream algorithm stays unchanged.

The operational image installs a tiny observer_transport facade pointing here;
legacy phase6 files/proofs are not modified. Exactly one request per socket, no
retries after uncertainty. No received path becomes a host filesystem path.
"""

import hashlib
import json
import socket
import struct
import time
from pathlib import Path
from uuid import UUID

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.observer import SearchControlAbort
from runtimes.openevolve_operational.bootstrap import (
    OperationalBootstrap,
    input_inventory,
)
from runtimes.openevolve_operational.checkpoint_transfer import (
    read_checkpoint,
    transfer_checkpoint,
)
from runtimes.openevolve_operational.fixed_input import read_fixed

BOOTSTRAP = Path("/bootstrap/bootstrap.json")
TICKET = Path("/ipc/ticket.json")
SOCKET = "/ipc/events.sock"
MAX_MESSAGE = 1048576
_SAVE_ORDINAL = 0
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
    }
)
UPLOADS = frozenset(
    {"checkpoint_upload_begin", "checkpoint_upload_chunk", "checkpoint_upload_finish"}
)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate transport key")
        result[key] = value
    return result


def read_record(path, limit):
    return json.loads(
        read_fixed(path, limit),
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )


def _bootstrap():
    raw = read_fixed(BOOTSTRAP, 262144)
    value = OperationalBootstrap.model_validate_json(raw)
    input_inventory(raw, read_fixed("/bootstrap/seed.py", 32768))
    value.remaining(time.time())
    return value


def bootstrap():
    value = _bootstrap()
    # Exact historical public projection expected by observer.worker_scope; the
    # typed operational envelope is independently validated, not silently parsed
    # as the old stored bootstrap format.
    return {
        "schema_version": "mineagent.openevolve-bootstrap.v1",
        "admission_id": str(value.admission_id),
        "epoch_id": str(value.epoch_id),
        "spec": value.spec.model_dump(mode="json"),
        "runtime_profile_sha256": value.runtime_profile_sha256,
        "config_sha256": value.config_sha256,
        "adapter_patch_sha256": value.adapter_patch_sha256,
    }


def process_ticket():
    value = read_record(TICKET, 256)
    if (
        type(value) is not dict
        or set(value) != {"ticket"}
        or type(value["ticket"]) is not str
        or len(value["ticket"]) != 64
        or any(c not in "0123456789abcdef" for c in value["ticket"])
    ):
        raise ValueError("fixed process ticket")
    return value["ticket"]


def read_exact(channel, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("original deadline")
        channel.settimeout(min(95, remaining))
        chunk = channel.recv(size - len(result))
        if not chunk:
            raise ValueError("truncated transport")
        result.extend(chunk)
    return bytes(result)


def _send(kind, payload, *, deadline):
    initial = _bootstrap()
    schema = (
        "mineagent.openevolve-operational-event.v1"
        if kind in UPLOADS | {"worker_placement"}
        else "mineagent.openevolve-event.v1"
    )
    event = {
        "schema_version": schema,
        "admission_id": str(initial.admission_id),
        "epoch_id": str(initial.epoch_id),
        "kind": kind,
        "payload": payload,
    }
    event_sha = hashlib.sha256(canonical(event)).hexdigest()
    wire = canonical(
        {"event": event, "event_sha256": event_sha, "process_ticket": process_ticket()}
    )
    if not 0 < len(wire) <= MAX_MESSAGE:
        raise ValueError("bounded event")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("original deadline")
        channel.settimeout(min(95, remaining))
        channel.connect(SOCKET)
        channel.sendall(struct.pack("!I", len(wire)) + wire)
        size = struct.unpack("!I", read_exact(channel, 4, deadline))[0]
        if not 0 < size <= 262144:
            raise ValueError("reply bound")
        raw = read_exact(channel, size, deadline)
    answer = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )
    if (
        canonical(answer) != raw
        or type(answer) is not dict
        or set(answer) != {"schema_version", "event_sha256", "receipt_id", "result"}
        or answer["schema_version"] != "mineagent.openevolve-event-result.v1"
        or answer["event_sha256"] != event_sha
        or str(UUID(answer["receipt_id"])) != answer["receipt_id"]
        or type(answer["result"]) is not dict
    ):
        raise ValueError("committed event ACK")
    if time.monotonic() >= deadline:
        raise ValueError("late ACK")
    return answer["result"]


def host_event(kind, payload):
    global _SAVE_ORDINAL
    if kind not in EVENTS or type(payload) is not dict:
        raise SearchControlAbort("worker_observation")
    try:
        deadline = time.monotonic() + _bootstrap().remaining(time.time())
        if kind == "checkpoint_prepare":
            if set(payload) != {"frontier", "state_sha256"} or _SAVE_ORDINAL >= 4:
                raise ValueError("closed initial epoch save sequence")
            # Each actual save gets a distinct immutable intent, even at the
            # same final cursor. No retry after an uncertain socket operation.
            _SAVE_ORDINAL += 1
            payload = {**payload, "save_ordinal": _SAVE_ORDINAL}
        if kind == "checkpoint_publish":
            token = UUID(payload["token"])
            if payload.get("path") != f"/work/checkpoints/{token}":
                raise ValueError("fixed checkpoint path")
            files = read_checkpoint("/work/checkpoints", token, deadline=deadline)
            transfer_checkpoint(
                lambda method, body: _send(method, body, deadline=deadline),
                token=token,
                files=files,
                deadline=deadline,
            )
        return _send(kind, payload, deadline=deadline)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise SearchControlAbort("worker_observation") from None


def send_placement(payload):
    try:
        deadline = time.monotonic() + _bootstrap().remaining(time.time())
        return _send("worker_placement", payload, deadline=deadline)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise SearchControlAbort("submission") from None
