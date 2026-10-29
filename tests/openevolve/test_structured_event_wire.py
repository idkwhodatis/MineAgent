"""Normal wire grammar only: hashes/tickets are never socket or run authority."""

import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.oe_structured_event_wire import event_reply, read_event, reread_event
from packages.openevolve_structured.checkpoint import canonical


def wire(kind="seed_source", tag="mineagent.oe-structured-provider-event.v1"):
    event = {
        "schema_version": tag,
        "admission_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "kind": kind,
        "payload": {"candidate_sha256": "a" * 64},
    }
    return canonical(
        {
            "event": event,
            "event_sha256": hashlib.sha256(canonical(event)).hexdigest(),
            "process_ticket": "b" * 64,
        }
    )


def test_actual_canonical_envelope_and_committed_reply_ids():
    event = read_event(wire())
    assert reread_event(event) is event
    receipt = uuid4()
    answer = json.loads(event_reply(event, receipt, {"acknowledged": True}))
    assert answer["event_sha256"] == event.event_sha256
    assert answer["receipt_id"] == str(receipt)
    assert "process_ticket" not in answer
    assert "bbbbbbbb" not in repr(event)


@pytest.mark.parametrize(
    "tag",
    [
        "mineagent.oe-structured-event.v1",
        "mineagent.openevolve-event.v1",
        "mineagent.oe-structured-provider-transfer-event.v1",
    ],
)
def test_old_or_wrong_profile_never_falls_back(tag):
    with pytest.raises(ValueError):
        read_event(wire(tag=tag))


@pytest.mark.parametrize(
    "change", ["extra", "hash", "ticket", "uuid", "kind", "whitespace", "duplicate"]
)
def test_wire_mutation_or_noncanonical_rejected(change):
    raw = wire()
    value = json.loads(raw)
    if change == "extra":
        value["unexpected"] = 1
    elif change == "hash":
        value["event_sha256"] = "0" * 64
    elif change == "ticket":
        value["process_ticket"] = True
    elif change == "uuid":
        value["event"]["epoch_id"] = value["event"]["epoch_id"].upper()
    elif change == "kind":
        value["event"]["kind"] = "checkpoint_load_prepare"
    if change == "whitespace":
        raw += b"\n"
    elif change == "duplicate":
        raw = raw.replace(b'{"event":', b'{"event":null,"event":', 1)
    else:
        raw = canonical(value)
    with pytest.raises(ValueError):
        read_event(raw)


def test_copied_projection_cannot_be_dispatched_as_original_event():
    event = read_event(wire())
    with pytest.raises(ValueError):
        reread_event(replace(event, epoch_id=uuid4()))
    with pytest.raises(ValueError):
        event_reply(event, "not-persisted-id", {})


def test_transfer_kind_has_only_explicit_normal_transfer_tag():
    event = read_event(
        wire(
            kind="worker_placement",
            tag="mineagent.oe-structured-provider-transfer-event.v1",
        )
    )
    assert event.kind == "worker_placement"
