"""Genuine event/evidence codecs, explicit DB/peer doubles; not PG acceptance."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_checkpoint_native import native as native_fixture
from test_event_projection import envelope, wire

from backend.app import openevolve_controller_events as s
from packages.openevolve_adapter.event_projection import parse_controller_event
from packages.openevolve_adapter.object_identity import parse_document
from runtimes.openevolve_operational.wire import parse_event


@pytest.fixture
def native(tmp_path):
    return native_fixture.__wrapped__(tmp_path)


def setup(monkeypatch, native):
    event = parse_event(wire(envelope(native)))
    calls, writes = [], {}
    seed, ack = NS(id=uuid4()), NS(id=uuid4())
    receipt = NS(id=uuid4(), control_state_sha256=event.payload["state_sha256"])

    class Session:
        def scalar(self, query):
            model = query.column_descriptions[0]["entity"]
            return {
                s.m.SearchCandidate: seed,
                s.m.SearchObjectiveAcknowledgement: ack,
            }.get(model)

    @contextmanager
    def transaction(*args, **kwargs):
        calls.append("begin")
        yield Session()
        calls.append("commit")

    def inspect(session, fence, peer, raw, **kw):
        assert kw["expected_process_profile"] == s.PROFILE
        calls.append("peer")
        return parse_controller_event(raw)

    def publish(factory, fence, *, kind, content, logical_key, settings):
        parse_document(kind, content)
        calls.append("publish")
        if logical_key in writes:
            obj, old = writes[logical_key]
            assert old == content
            return obj
        obj = NS(id=uuid4(), sha256=hashlib.sha256(content).hexdigest())
        writes[logical_key] = obj, content
        return obj

    def record(session, fence, peer, raw, **kwargs):
        calls.append("record")
        assert kwargs["command"].seed_objective_ack_id == ack.id
        assert kwargs["command"].state.sha256 == event.payload["state_sha256"]
        return receipt

    monkeypatch.setattr(s, "neutral_transaction", transaction)
    monkeypatch.setattr(s, "inspect_authenticated_controller_event", inspect)
    monkeypatch.setattr(s, "publish_search_object", publish)
    monkeypatch.setattr(s, "record_controller_event", record)
    return event, calls, writes, receipt


def test_retained_event_population_then_fresh_peer_then_committed_ack(
    monkeypatch, native
):
    event, calls, writes, receipt = setup(monkeypatch, native)
    identity, answer = s.apply_controller_event(
        None, None, None, event=event, ticket_key=b"k" * 32, settings=None
    )
    assert identity == receipt.id
    assert answer == {"frontier": 0, "state_sha256": event.payload["state_sha256"]}
    assert len(writes) == 2
    assert calls == [
        "begin",
        "peer",
        "commit",
        "publish",
        "publish",
        "begin",
        "peer",
        "record",
        "commit",
    ]


def test_exact_lost_ack_reuses_retained_objects(monkeypatch, native):
    event, calls, writes, _receipt = setup(monkeypatch, native)
    args = {"event": event, "ticket_key": b"k" * 32, "settings": None}
    first = s.apply_controller_event(None, None, None, **args)
    assert s.apply_controller_event(None, None, None, **args) == first
    assert len(writes) == 2
    assert calls.count("peer") == 4


def test_death_after_publication_no_semantic_write_or_ack(monkeypatch, native):
    event, calls, writes, _receipt = setup(monkeypatch, native)
    original = s.inspect_authenticated_controller_event

    def inspect(*args, **kwargs):
        if writes:
            raise ValueError("actual peer exited")
        return original(*args, **kwargs)

    monkeypatch.setattr(s, "inspect_authenticated_controller_event", inspect)
    with pytest.raises(ValueError, match="peer exited"):
        s.apply_controller_event(
            None, None, None, event=event, ticket_key=b"k" * 32, settings=None
        )
    assert len(writes) == 2
    assert "record" not in calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch_id", uuid4()),
        ("admission_id", uuid4()),
        ("event_sha256", "0" * 64),
        ("payload", {}),
    ],
)
def test_detached_event_metadata_cannot_replace_wire(monkeypatch, native, field, value):
    event, calls, writes, _receipt = setup(monkeypatch, native)
    with pytest.raises(ValueError):
        s.apply_controller_event(
            None,
            None,
            None,
            event=replace(event, **{field: value}),
            ticket_key=b"k" * 32,
            settings=None,
        )
    assert not calls and not writes


@pytest.mark.parametrize("key", [None, "k" * 32, b"k" * 31, b"k" * 33, True])
def test_host_key_closed_before_effect(monkeypatch, native, key):
    event, calls, writes, _receipt = setup(monkeypatch, native)
    with pytest.raises(ValueError):
        s.apply_controller_event(
            None, None, None, event=event, ticket_key=key, settings=None
        )
    assert not calls and not writes


def test_ticket_exact_replay_and_scope_separation():
    epoch, intent = uuid4(), uuid4()
    value = s._ticket(b"k" * 32, epoch, intent)
    assert len(value) == 64 and s._ticket(b"k" * 32, epoch, intent) == value
    assert s._ticket(b"l" * 32, epoch, intent) != value
    assert s._ticket(b"k" * 32, uuid4(), intent) != value
    assert s._ticket(b"k" * 32, epoch, uuid4()) != value
