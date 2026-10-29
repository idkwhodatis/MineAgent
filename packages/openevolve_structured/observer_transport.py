"""Distinct027 event/transfer protocol, not a legacy024 transport overlay.

Exactly one request per socket, original deadline bounded; no retry or provider fallback.
The host must resolve fresh persisted scope and input ownership independently.
"""

import hashlib
import json
import socket
import struct
import time
from pathlib import Path
from uuid import UUID

from packages.openevolve_structured.candidate import project_candidate
from packages.openevolve_structured.checkpoint import canonical
from packages.openevolve_structured.contracts import StructuredBootstrap
from packages.openevolve_structured.observer import SearchControlAbort
from runtimes.openevolve_operational.fixed_input import read_fixed
from runtimes.openevolve_structured.checkpoint_transfer import (
    read_checkpoint,
    transfer_checkpoint,
)

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
    header = json.loads(raw, object_pairs_hook=unique)
    if type(header) is not dict:
        raise ValueError("Structured bootstrap object required")
    tag = header.get("schema_version")
    if tag in {
        "mineagent.oe-structured-bootstrap.v2",
        "mineagent.oe-structured-bootstrap.v3",
    }:
        from .workflow_bootstrap import parse_provider_bootstrap

        value = parse_provider_bootstrap(raw)
    else:
        # Strict v1 parser rejects unknown tags; there is no permissive fallback.
        value = StructuredBootstrap.model_validate_json(raw)
    seed = read_fixed("/bootstrap/seed.json", 32768)
    candidate = project_candidate(seed, value.spec.domain)
    if (
        candidate.candidate_sha256 != value.spec.seed_candidate_sha256
        or canonical(value.model_dump(mode="json")) != raw
    ):
        raise ValueError("Structured bootstrap/seed commitment differs")
    from packages.factors.contracts import digest
    from runtimes.openevolve_structured.profile import (
        profile_sha256 as physical_profile_sha256,
    )

    if tag in {
        "mineagent.oe-structured-bootstrap.v2",
        "mineagent.oe-structured-bootstrap.v3",
    }:
        from .provider_bootstrap import runtime_profile_sha256
        from .provider_runtime_config import build_config, config_manifest

        config = build_config(value.spec, seed, value.model_binding)
        from .workflow_bootstrap import (
            WorkflowStructuredBootstrap,
            workflow_runtime_profile_sha256,
        )

        runtime_sha = (
            workflow_runtime_profile_sha256()
            if type(value) is WorkflowStructuredBootstrap
            else runtime_profile_sha256()
        )
    else:
        from .runtime_config import build_config, config_manifest
        from .runtime_profile import profile_sha256

        config = build_config(value.spec, seed)
        runtime_sha = profile_sha256()
    if (
        value.physical_profile_sha256 != physical_profile_sha256()
        or value.runtime_profile_sha256 != runtime_sha
        or value.config_sha256 != digest(config_manifest(config))
    ):
        raise ValueError("Structured runtime/config commitment differs")
    value.remaining(time.time())
    return value


def bootstrap():
    return _bootstrap().model_dump(mode="json")


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
    normal = initial.schema_version in {
        "mineagent.oe-structured-bootstrap.v2",
        "mineagent.oe-structured-bootstrap.v3",
    }
    prefix = "mineagent.oe-structured-provider" if normal else "mineagent.oe-structured"
    schema = prefix + (
        "-transfer-event.v1" if kind in UPLOADS | {"worker_placement"} else "-event.v1"
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
        or answer["schema_version"] != prefix + "-event-result.v1"
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
