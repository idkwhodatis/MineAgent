"""Closed normal027 wire envelope, distinct from fixture and Python024 protocols.

Parsing provides data only. The dispatcher additionally needs an actual current
nonserializable Peer; neither a ticket nor a correctly hashed event is authority.
"""

import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID

from packages.openevolve_structured.checkpoint import canonical

EVENTS = frozenset(
    {
        "iteration_intent",
        "worker_started",
        "worker_outcome",
        "proposal_response",
        "proposal_source",
        "candidate_intent",
        "seed_source",
        "execute_evaluation",
        "evaluation_complete",
        "rendered_prompt",
        "terminal_applied",
        "checkpoint_prepare",
        "checkpoint_publish",
    }
)
TRANSFERS = frozenset(
    {
        "worker_placement",
        "checkpoint_upload_begin",
        "checkpoint_upload_chunk",
        "checkpoint_upload_finish",
    }
)


@dataclass(frozen=True)
class NormalEvent:
    admission_id: UUID
    epoch_id: UUID
    kind: str
    payload: dict
    raw_event: bytes
    payload_raw: bytes
    event_sha256: str
    process_ticket: str = field(repr=False)


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate event key")
        result[key] = value
    return result


def _sha(value):
    return (
        type(value) is str
        and len(value) == 64
        and not set(value) - set("0123456789abcdef")
    )


def read_event(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        raise ValueError("Bounded actual event bytes required")
    value = json.loads(
        raw,
        object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite event")),
    )
    if (
        type(value) is not dict
        or set(value) != {"event", "event_sha256", "process_ticket"}
        or canonical(value) != raw
    ):
        raise ValueError("Exact normal event envelope required")
    event = value["event"]
    if type(event) is not dict or set(event) != {
        "schema_version",
        "admission_id",
        "epoch_id",
        "kind",
        "payload",
    }:
        raise ValueError("Exact normal event fields required")
    kind = event["kind"]
    if (
        type(kind) is not str
        or kind not in EVENTS | TRANSFERS
        or type(event["payload"]) is not dict
    ):
        raise ValueError("Unsupported normal event")
    expected = "mineagent.oe-structured-provider-" + (
        "transfer-event.v1" if kind in TRANSFERS else "event.v1"
    )
    event_raw = canonical(event)
    if (
        event["schema_version"] != expected
        or not _sha(value["event_sha256"])
        or hashlib.sha256(event_raw).hexdigest() != value["event_sha256"]
        or not _sha(value["process_ticket"])
    ):
        raise ValueError("Normal event identity differs")
    ids = []
    for key in ("admission_id", "epoch_id"):
        if type(event[key]) is not str or str(UUID(event[key])) != event[key]:
            raise ValueError("Canonical event UUID required")
        ids.append(UUID(event[key]))
    return NormalEvent(
        *ids,
        kind,
        event["payload"],
        event_raw,
        canonical(event["payload"]),
        value["event_sha256"],
        value["process_ticket"],
    )


def reread_event(event):
    """Do not trust an unchecked dataclass constructed/copied by an internal caller."""
    if type(event) is not NormalEvent:
        raise ValueError("Exact parsed normal event required")
    raw = canonical(
        {
            "event": json.loads(event.raw_event),
            "event_sha256": event.event_sha256,
            "process_ticket": event.process_ticket,
        }
    )
    if read_event(raw) != event:
        raise ValueError("Parsed event projection differs")
    return event


def event_reply(event, receipt_id, result):
    reread_event(event)
    if type(receipt_id) is not UUID or type(result) is not dict:
        raise ValueError("Real committed receipt identity required")
    raw = canonical(
        {
            "schema_version": "mineagent.oe-structured-provider-event-result.v1",
            "event_sha256": event.event_sha256,
            "receipt_id": str(receipt_id),
            "result": result,
        }
    )
    if not 0 < len(raw) <= 262144:
        raise ValueError("Bounded normal event ACK required")
    return raw
