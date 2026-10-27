"""Strict event bytes/ticket binding with explicit transaction/row doubles."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_event_identity as s
from packages.openevolve_adapter.observer_transport import canonical
from runtimes.openevolve_operational.wire import parse_event


def event():
    value = {
        "schema_version": "mineagent.openevolve-event.v1",
        "admission_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "kind": "checkpoint_prepare",
        "payload": {"frontier": 0, "state_sha256": "b" * 64},
    }
    return parse_event(
        canonical(
            {
                "event": value,
                "event_sha256": hashlib.sha256(canonical(value)).hexdigest(),
                "process_ticket": "a" * 64,
            }
        )
    )


def setup(monkeypatch):
    calls = []
    monkeypatch.setattr(
        s, "require_neutral_transaction", lambda *a: calls.append("fence")
    )
    monkeypatch.setattr(
        s,
        "_owned_row",
        lambda *a: NS(ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest()),
    )
    return calls


def test_exact_event_ticket_requires_current_transaction(monkeypatch):
    calls = setup(monkeypatch)
    value = event()
    s.require_event_ticket(None, None, value)
    assert calls == ["fence"]
    assert parse_event(s.exact_event(value)) == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch_id", uuid4()),
        ("admission_id", uuid4()),
        ("payload", {}),
        ("kind", "checkpoint_publish"),
        ("event_sha256", "0" * 64),
    ],
)
def test_detached_event_fields_rejected(monkeypatch, field, value):
    setup(monkeypatch)
    with pytest.raises(ValueError):
        s.require_event_ticket(None, None, replace(event(), **{field: value}))


def test_valid_peer_cannot_substitute_ticket(monkeypatch):
    setup(monkeypatch)
    with pytest.raises(ValueError, match="ticket differs"):
        s.require_event_ticket(None, None, replace(event(), process_ticket="c" * 64))


def test_stale_fence_fails_before_row_lookup(monkeypatch):
    setup(monkeypatch)

    def deny(*args):
        raise ValueError("stale fence")

    monkeypatch.setattr(s, "require_neutral_transaction", deny)
    monkeypatch.setattr(s, "_owned_row", lambda *a: pytest.fail("must not read"))
    with pytest.raises(ValueError, match="stale fence"):
        s.require_event_ticket(None, None, event())
