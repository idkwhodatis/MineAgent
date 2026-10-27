"""Exact wire/context/host schedule with explicit DB and transport doubles."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_broker_rpc as rpc
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.research_runtime.engine_broker_contracts import OEChatResponse
from runtimes.openevolve_operational import peer as peers
from tests.openevolve.test_operational_peer import auth, case  # noqa:F401


def setup(case, monkeypatch):  # noqa:F811
    monkeypatch.setattr(peers, "_credentials", lambda _: (102, 65533, 65533))
    peer = auth(case, binding=case.worker_binding, worker_ticket=case.worker_ticket)
    context = SearchOperationContext(
        owner_id=case.fence.owner_id,
        run_id=case.fence.run_id,
        epoch_id=peer.binding.epoch_id,
        iteration_intent_id=peer.binding.intent_id,
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=uuid4(),
        parent_source_sha256="b" * 64,
        selection_snapshot_sha256="c" * 64,
        rendered_context_sha256="d" * 64,
    )
    request = {
        "kind": "chat",
        "model": "openevolve-fixture.v1",
        "messages": [
            {"role": "system", "content": "fixed"},
            {"role": "user", "content": "mutation"},
        ],
        "max_output_tokens": 32768,
        "temperature_millionths": 0,
    }
    value = {
        "schema_version": "mineagent.openevolve-broker-call.v1",
        "admission_id": str(case.admission.id),
        "process_ticket": case.ticket,
        "context": context.model_dump(mode="json"),
        "context_ticket": rpc.context_ticket(b"k" * 32, context),
        "request": request,
        "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
    }
    intent = NS(
        id=context.iteration_intent_id,
        native_iteration=1,
        parent_candidate_id=context.parent_candidate_id,
        parent_source_sha256=context.parent_source_sha256,
        selection_sha256=context.selection_snapshot_sha256,
    )
    calls, receipt = [], uuid4()

    @contextmanager
    def transaction(*_, **__):
        calls.append("begin")
        yield object()
        calls.append("commit")

    monkeypatch.setattr(rpc, "neutral_transaction", transaction)
    monkeypatch.setattr(rpc, "_owned_row", lambda *args: intent)
    monkeypatch.setattr(rpc, "_open_worker", lambda *args: calls.append("peer"))

    def submit(*args, **kwargs):
        assert calls == ["begin", "peer", "commit"]
        assert kwargs["request_bytes"] == canonical(request)
        calls.append("submit")
        return (
            receipt,
            kwargs["fixture_response"].content,
            {"prompt_tokens": 13, "completion_tokens": 1, "total_tokens": 14},
        )

    monkeypatch.setattr(rpc, "submit_fixture_request", submit)
    return peer, value, calls, receipt


def dispatch(case, peer, value):  # noqa:F811
    return rpc.dispatch_broker(
        None,
        case.fence,
        peer,
        raw=canonical(value),
        ticket_key=b"k" * 32,
        schedule=rpc.FixtureSchedule((OEChatResponse(content="fixed patch"),)),
        settings=None,
    )


def test_exact_context_and_actual_retained_receipt(case, monkeypatch):  # noqa:F811
    import json

    peer, value, calls, receipt = setup(case, monkeypatch)
    result = json.loads(dispatch(case, peer, value))
    assert result["receipt_id"] == str(receipt)
    assert result["content"] == "fixed patch"
    assert (
        result["context_sha256"]
        == hashlib.sha256(canonical(value["context"])).hexdigest()
    )
    assert calls[-1] == "submit"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("schema_version", "neutral"),
        ("process_ticket", "f" * 64),
        ("context_ticket", "e" * 64),
        ("request_sha256", "0" * 64),
        ("admission_id", str(uuid4())),
        ("extra", True),
    ],
)
def test_drift_before_submission(case, monkeypatch, field, bad):  # noqa:F811
    peer, value, calls, _ = setup(case, monkeypatch)
    value[field] = bad
    with pytest.raises(ValueError):
        dispatch(case, peer, value)
    assert "submit" not in calls


def test_arbitrary_provider_callable_rejected(case, monkeypatch):  # noqa:F811
    peer, value, calls, _ = setup(case, monkeypatch)
    with pytest.raises(ValueError):
        rpc.dispatch_broker(
            None,
            case.fence,
            peer,
            raw=canonical(value),
            ticket_key=b"k" * 32,
            schedule=lambda: "response",
            settings=None,
        )
    assert not calls
