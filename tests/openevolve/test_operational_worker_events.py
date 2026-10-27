"""Real worker/event codecs with explicit DB/peer doubles; not PG or IPC proof."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_worker_events as s
from packages.openevolve_adapter.host_contracts import RenderedProjection
from packages.openevolve_adapter.object_identity import parse_document
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from runtimes.openevolve_operational.wire import parse_event


def event(kind, **payload):
    grant = IterationGrant(
        schema_version="mineagent.openevolve-iteration-grant.v1",
        admission_id=uuid4(),
        epoch_id=uuid4(),
        intent_id=uuid4(),
        intent_ticket="a" * 64,
        native_iteration=1,
        parent_candidate_id=uuid4(),
        parent_source_sha256="b" * 64,
        selection_sha256="c" * 64,
    )
    value = {
        "schema_version": "mineagent.openevolve-event.v1",
        "admission_id": str(grant.admission_id),
        "epoch_id": str(grant.epoch_id),
        "kind": kind,
        "payload": {"grant": grant.model_dump(mode="json"), **payload},
    }
    return parse_event(
        canonical(
            {
                "event": value,
                "event_sha256": hashlib.sha256(canonical(value)).hexdigest(),
                "process_ticket": "d" * 64,
            }
        )
    )


def setup(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    calls, objects, rows = [], {}, {}
    applied = []

    class Session:
        def scalar(self, query):
            return rows.get(query.column_descriptions[0]["entity"])

        def scalars(self, query):
            return NS(all=lambda: rows.get(query.column_descriptions[0]["entity"], []))

        def add(self, row):
            rows[type(row)] = row
            calls.append("add")

        def flush(self):
            calls.append("flush")

    @contextmanager
    def transaction(*_, **__):
        calls.append("begin")
        yield Session()
        calls.append("commit")

    def publish(*_, kind, content, logical_key, settings):
        parse_document(kind, content)
        calls.append("publish")
        if logical_key in objects:
            previous, raw = objects[logical_key]
            assert raw == content
            return previous
        value = NS(id=uuid4(), sha256=hashlib.sha256(content).hexdigest())
        objects[logical_key] = (value, content)
        return value

    result = NS(id=uuid4())

    def apply(session, current, peer, grant, command, kind, settings):
        assert current is fence
        type(command).model_validate_json(command.model_dump_json())
        applied.append((kind, command))
        calls.append("apply")
        return result

    monkeypatch.setattr(s, "neutral_transaction", transaction)
    monkeypatch.setattr(s, "require_event_ticket", lambda *_: calls.append("ticket"))
    monkeypatch.setattr(s, "_open_worker", lambda *_: calls.append("peer"))
    monkeypatch.setattr(s, "publish_search_object", publish)
    monkeypatch.setattr(s, "_apply", apply)
    monkeypatch.setattr(s, "_owned_row", lambda _session, model, *_: rows[model])
    return fence, calls, objects, rows, applied, result


def prompt_event():
    projection = RenderedProjection(
        system="Fixed system",
        user="Exact rendered user",
        references=(),
        current_source_sha256="b" * 64,
        artifact_section_sha256="e" * 64,
    )
    raw = canonical(projection.model_dump(mode="json"))
    return event(
        "rendered_prompt",
        projection=projection.model_dump(mode="json"),
        rendered_sha256=hashlib.sha256(raw).hexdigest(),
    )


def test_render_retains_bytes_then_reauthenticates_then_commits_ack(monkeypatch):
    fence, calls, objects, rows, applied, result = setup(monkeypatch)
    receipt, answer = s.render_prompt(
        None, fence, None, event=prompt_event(), ticket_key=b"k" * 32, settings=None
    )
    assert receipt == result.id and len(answer["context_ticket"]) == 64
    assert len(objects) == len(applied) == 1
    assert calls == [
        "begin",
        "ticket",
        "peer",
        "commit",
        "publish",
        "begin",
        "ticket",
        "peer",
        "add",
        "flush",
        "apply",
        "commit",
    ]
    context = rows[s.ResearchOperationContext]
    assert context.context == answer["context"]
    assert applied[0][1].context_id == context.id


def test_render_exact_retry_preserves_context_ticket_and_object(monkeypatch):
    fence, calls, objects, rows, _, _ = setup(monkeypatch)
    value = prompt_event()
    kwargs = {"event": value, "ticket_key": b"k" * 32, "settings": None}
    first = s.render_prompt(None, fence, None, **kwargs)
    assert s.render_prompt(None, fence, None, **kwargs) == first
    assert len(objects) == 1 and calls.count("add") == 1
    assert len(rows) == 1 and calls.count("peer") == 4


def test_peer_loss_after_prompt_storage_never_applies_or_acks(monkeypatch):
    fence, calls, objects, _, applied, _ = setup(monkeypatch)

    def peer(*_):
        if objects:
            raise ValueError("dead peer")
        calls.append("peer")

    monkeypatch.setattr(s, "_open_worker", peer)
    with pytest.raises(ValueError, match="dead peer"):
        s.render_prompt(
            None, fence, None, event=prompt_event(), ticket_key=b"k" * 32, settings=None
        )
    assert len(objects) == 1 and not applied and calls[-1] != "commit"


@pytest.mark.parametrize(
    "field,value",
    [
        ("payload", {}),
        ("event_sha256", "f" * 64),
        ("epoch_id", uuid4()),
        ("admission_id", uuid4()),
    ],
)
def test_mutated_event_rejected_before_effect(monkeypatch, field, value):
    fence, calls, objects, _, applied, _ = setup(monkeypatch)
    with pytest.raises(ValueError):
        s.render_prompt(
            None,
            fence,
            None,
            event=replace(prompt_event(), **{field: value}),
            ticket_key=b"k" * 32,
            settings=None,
        )
    assert not calls and not objects and not applied


@pytest.mark.parametrize("key", [None, b"short", "k" * 32, b"k" * 33])
def test_invalid_host_context_key_rejected_before_effect(monkeypatch, key):
    fence, calls, objects, _, _, _ = setup(monkeypatch)
    with pytest.raises(ValueError):
        s.render_prompt(
            None, fence, None, event=prompt_event(), ticket_key=key, settings=None
        )
    assert not calls and not objects


def test_started_worker_ack_follows_commit(monkeypatch):
    fence, calls, _, _, _, result = setup(monkeypatch)

    def bind(*_, **kwargs):
        assert kwargs["expected_process_profile"] == s.PROFILE
        calls.append("bind")
        return result

    monkeypatch.setattr(s, "bind_started_worker", bind)
    assert s.start_worker(None, fence, None, event=event("worker_started")) == (
        result.id,
        {"started": True},
    )
    assert calls == ["begin", "ticket", "bind", "commit"]


def response_rows(rows):
    rows[s.m.SearchRenderedContext] = NS(id=uuid4(), context_id=uuid4())
    rows[s.ResearchModelOperation] = NS(
        id=uuid4(), state="completed", protocol="mineagent.openevolve-chat.v1"
    )
    rows[s.ResearchModelTransport] = [NS(id=uuid4(), ordinal=1, state="completed")]
    rows[s.ResearchModelOutcome] = NS(
        id=uuid4(), outcome="completed", response_sha256="e" * 64
    )


def test_response_uses_exact_completed_retained_outcome(monkeypatch):
    fence, calls, _, rows, applied, result = setup(monkeypatch)
    response_rows(rows)
    answer = s.observe_proposal_response(
        None,
        fence,
        None,
        event=event("proposal_response", response="actual response"),
        settings=None,
    )
    assert answer == (result.id, {"recorded": True}) and calls[-1] == "commit"
    assert applied[0][1].model_outcome_id == rows[s.ResearchModelOutcome].id
    assert applied[0][1].response_text == "actual response"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_context",
        "wrong_protocol",
        "pending_transport",
        "duplicate_transport",
        "missing_outcome",
    ],
)
def test_incomplete_response_chain_does_not_apply(monkeypatch, failure):
    fence, _, _, rows, applied, _ = setup(monkeypatch)
    response_rows(rows)
    if failure == "missing_context":
        rows.pop(s.m.SearchRenderedContext)
    if failure == "wrong_protocol":
        rows[s.ResearchModelOperation].protocol = "mineagent.rdagent-chat.v1"
    if failure == "pending_transport":
        rows[s.ResearchModelTransport][0].state = "pending"
    if failure == "duplicate_transport":
        rows[s.ResearchModelTransport] *= 2
    if failure == "missing_outcome":
        rows.pop(s.ResearchModelOutcome)
    with pytest.raises(ValueError):
        s.observe_proposal_response(
            None,
            fence,
            None,
            event=event("proposal_response", response="response"),
            settings=None,
        )
    assert not applied


def source_event():
    raw = b"pass\n"
    return event(
        "proposal_source",
        stage="applied",
        source=raw.decode(),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_bytes=len(raw),
        evidence="complete_source",
    )


def test_source_retains_exact_bytes_then_fresh_peer_and_response_binding(monkeypatch):
    fence, calls, objects, rows, applied, result = setup(monkeypatch)
    rows[s.m.SearchProposalResponse] = NS(id=uuid4())
    assert s.observe_proposal_source(
        None, fence, None, event=source_event(), settings=None
    ) == (result.id, {"recorded": True})
    assert len(objects) == 1 and calls.count("peer") == 2 and calls[-1] == "commit"
    assert applied[0][1].proposal_id == rows[s.m.SearchProposalResponse].id


def test_source_without_response_keeps_evidence_but_no_semantic_write(monkeypatch):
    fence, calls, objects, _, applied, _ = setup(monkeypatch)
    with pytest.raises(ValueError, match="actual response"):
        s.observe_proposal_source(
            None, fence, None, event=source_event(), settings=None
        )
    assert len(objects) == 1 and not applied and calls[-1] != "commit"
