"""Accounting and orchestration-double tests; no database/Docker acceptance."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_operational_broker as module
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    RDEmbeddingResponse,
    canonical_bytes,
    parse_request,
)

CHAT = "mineagent.rdagent-chat.v1"
EMBED = "mineagent.rdagent-embedding.v1"


def request(protocol=CHAT, tokens=4096):
    values = (
        {
            "messages": [{"role": "user", "content": "中a"}],
            "max_output_tokens": tokens,
            "response_format": None,
        }
        if protocol == CHAT
        else {"inputs": ["中a", "b"], "dimensions": 2}
    )
    return canonical_bytes(protocol, parse_request(protocol, values))


@pytest.mark.parametrize("size,units", [(1, 1), (128, 1), (129, 2), (256, 2), (257, 3)])
def test_fixture_chat_accounting_exact_byte_boundaries(size, units):
    _, usage = module.fixture_usage(
        CHAT, request(), RDChatResponse(content="a" * size, finish_reason="stop")
    )
    assert usage.calls == 1 and usage.tokens == 4 + units
    assert usage.storage_bytes == usage.wall_milliseconds == 0


def test_unicode_response_bytes_not_characters():
    _, usage = module.fixture_usage(
        CHAT, request(), RDChatResponse(content="中" * 43, finish_reason="length")
    )
    assert usage.tokens == 6


def test_embedding_fixed_cardinality_charge():
    response = RDEmbeddingResponse(vectors=((0.0, 1.0), (1.0, 0.0)))
    _, usage = module.fixture_usage(EMBED, request(EMBED), response)
    assert usage.tokens == 5 and usage.calls == 1
    with pytest.raises(ValueError):
        module.fixture_usage(
            EMBED, request(EMBED), RDEmbeddingResponse(vectors=((0.0, 1.0),))
        )


def test_overbudget_response_rejected_before_submission():
    with pytest.raises(ValueError, match="exceeds reserved"):
        module.fixture_usage(
            CHAT,
            request(tokens=1),
            RDChatResponse(content="a" * 129, finish_reason="stop"),
        )


@pytest.mark.parametrize(
    "protocol,raw",
    [
        (CHAT, b"{}"),
        (CHAT, request() + b" "),
        ("mineagent.neutral-model.v1", request()),
        (CHAT, {}),
    ],
)
def test_noncanonical_or_wrong_protocol_rejected(protocol, raw):
    with pytest.raises(ValueError):
        module.fixture_usage(
            protocol, raw, RDChatResponse(content="x", finish_reason="stop")
        )


@pytest.mark.parametrize("value", [True, None, uuid4(), SimpleNamespace()])
def test_boolean_or_uuid_callback_cannot_grant_authority(value):
    with pytest.raises(ValueError, match="authenticated controller"):
        module._authorize(lambda session, fence: value, object(), object())


@pytest.fixture
def orchestration(monkeypatch):
    events = []
    session = object()
    state = {
        "active": False,
        "auth_count": 0,
        "fail_auth": None,
        "fail_response": False,
    }

    @contextmanager
    def transaction(factory, fence, **kwargs):
        assert not state["active"]
        state["active"] = True
        events.append("begin")
        try:
            yield session
        finally:
            state["active"] = False
            events.append("end")

    def auth(callback, actual_session, fence):
        assert state["active"] and actual_session is session
        state["auth_count"] += 1
        events.append("auth")
        if state["fail_auth"] == state["auth_count"]:
            raise ValueError("dead controller")

    def publish(factory, fence, settings, kind, content, key):
        assert not state["active"]
        events.append(kind)
        if kind == "model_response" and state["fail_response"]:
            raise OSError("uncertain publication")
        return uuid4()

    def admit(*args, **kwargs):
        assert state["active"]
        events.append("admit")
        return tuple(SimpleNamespace(id=uuid4()) for _ in range(3))

    def submitted(*args):
        assert state["active"]
        events.append("submitted")

    def settle(*args, **kwargs):
        assert state["active"]
        events.append("settled")
        assert kwargs["outcome"] == "completed"
        assert kwargs["usage"].tokens == 5
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(module, "neutral_transaction", transaction)
    monkeypatch.setattr(module, "_authorize", auth)
    monkeypatch.setattr(module, "_replay", lambda *a, **k: None)
    monkeypatch.setattr(module, "_publish", publish)
    monkeypatch.setattr(module, "admit_atomic_request", admit)
    monkeypatch.setattr(module, "mark_submitted", submitted)
    monkeypatch.setattr(module, "settle_transport", settle)
    kwargs = {
        "call_id": uuid4(),
        "idempotency_key": uuid4(),
        "relation": "initial",
        "protocol": CHAT,
        "request_bytes": request(),
        "fixture_response": RDChatResponse(content="x", finish_reason="stop"),
        "settings": object(),
        "revalidate_controller": lambda *a: None,
    }
    return events, state, kwargs


def test_auth_only_before_execution_completion_survives_controller_exit(orchestration):
    events, state, kwargs = orchestration
    state["fail_auth"] = 4
    result = module.submit_fixture_request(object(), object(), **kwargs)
    assert result.response_bytes == canonical_bytes(CHAT, kwargs["fixture_response"])
    assert state["auth_count"] == 3
    assert events == [
        "begin",
        "auth",
        "end",
        "model_request",
        "begin",
        "auth",
        "admit",
        "end",
        "begin",
        "auth",
        "submitted",
        "end",
        "model_response",
        "begin",
        "settled",
        "end",
    ]


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_lost_authority_never_submits(orchestration, failure):
    events, state, kwargs = orchestration
    state["fail_auth"] = failure
    with pytest.raises(ValueError, match="dead controller"):
        module.submit_fixture_request(object(), object(), **kwargs)
    assert "submitted" not in events and "settled" not in events


def test_failed_response_publication_never_fabricates_completion(orchestration):
    events, state, kwargs = orchestration
    state["fail_response"] = True
    with pytest.raises(OSError, match="uncertain publication"):
        module.submit_fixture_request(object(), object(), **kwargs)
    assert "submitted" in events and "settled" not in events


def test_complete_replay_does_not_publish_resubmit_or_charge(
    orchestration, monkeypatch
):
    events, _, kwargs = orchestration
    result = object()
    monkeypatch.setattr(module, "_replay", lambda *a, **k: result)
    assert module.submit_fixture_request(object(), object(), **kwargs) is result
    assert events == ["begin", "auth", "end"]
