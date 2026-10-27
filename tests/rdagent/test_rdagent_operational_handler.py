"""Fixed router and actual request/response codec tests; DB/authority are doubles."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app import rdagent_operational_broker as broker
from backend.app import rdagent_operational_evaluation as evaluation
from backend.app import rdagent_operational_handler as module
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    RDEmbeddingResponse,
)
from runtimes.rdagent_operational.rpc import RPCError


@pytest.fixture
def router(monkeypatch):
    session = object()

    @contextmanager
    def transaction(*args, **kwargs):
        yield session

    monkeypatch.setattr(module, "neutral_transaction", transaction)
    authorize = Mock()
    monkeypatch.setattr(module, "require_rd_controller", authorize)
    call = SimpleNamespace(phase="generation", start_id=uuid4(), kind="chat")
    monkeypatch.setattr(module, "_scope_row", lambda *a: call)
    submissions = []

    def submit(*args, **kwargs):
        submissions.append(kwargs)
        module.require_rd_controller(session, args[1], kwargs["controller_peer"])
        return SimpleNamespace(
            operation_id=uuid4(),
            response_bytes=kwargs["fixture_response"].model_dump_json().encode(),
        )

    monkeypatch.setattr(broker, "submit_fixture_request", submit)
    monkeypatch.setattr(
        evaluation,
        "measured_start_snapshot",
        lambda *a: (SimpleNamespace(snapshot_id=uuid4(), rows=(1, 2, 3)), True),
    )
    return SimpleNamespace(
        handler=module.OperationalHandler(object(), object(), object()),
        peer=object(),
        call=call,
        submissions=submissions,
        authorize=authorize,
    )


def body(embedding=False):
    request = (
        {
            "kind": "embedding",
            "model": "fixture.rdagent.v1",
            "inputs": ["first", "second"],
            "dimensions": 2,
        }
        if embedding
        else {
            "kind": "chat",
            "model": "fixture.rdagent.v1",
            "messages": [{"role": "user", "content": "fixture"}],
            "max_output_tokens": 4096,
            "temperature_millionths": 0,
            "response_format": {"type": "json_object"},
        }
    )
    return {
        "exchange_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "relation": "initial",
        "protocol": "mineagent.rdagent-embedding.v1"
        if embedding
        else "mineagent.rdagent-chat.v1",
        "request_utf8": json.dumps(request),
    }


@pytest.mark.parametrize(
    "method", ["semantic_retry", "reject_source", "arbitrary", "sql"]
)
def test_closed_methods(router, method):
    with pytest.raises(RPCError, match="unsupported"):
        router.handler(router.peer, method, {})
    router.authorize.assert_called_once()
    assert router.submissions == []


def test_check_control_exact_shape_and_returned(router):
    assert router.handler(router.peer, "check_control", {}) == {"current": True}
    with pytest.raises(RPCError, match="fixed_method_body"):
        router.handler(router.peer, "check_control", {"authority": True})
    router.handler.returned = True
    with pytest.raises(RPCError, match="returned_controller"):
        router.handler(router.peer, "check_control", {})


@pytest.mark.parametrize(
    "key,value",
    [
        ("protocol", "mineagent.neutral-model.v1"),
        ("relation", "continuation"),
        ("request_utf8", {}),
        ("extra", 1),
    ],
)
def test_exact_initial_request_profile(router, key, value):
    data = body()
    data[key] = value
    with pytest.raises((RPCError, ValueError)):
        router.handler(router.peer, "submit_chat", data)
    assert router.submissions == []


def test_persisted_kind_not_caller_method(router):
    router.call.kind = "embedding"
    with pytest.raises(RPCError, match="persisted_call_kind"):
        router.handler(router.peer, "submit_chat", body())
    assert router.submissions == []


@pytest.mark.parametrize(
    "phase", ["generation", "output_format", "critique", "final_decision"]
)
def test_fixed_chat_response_matches_actual_codec(router, phase):
    router.call.phase = phase
    result = router.handler(router.peer, "submit_chat", body())
    response = RDChatResponse.model_validate(result["response"])
    assert response.finish_reason == "stop"
    if phase == "generation":
        code = json.loads(response.content)["code"]
        expected = (
            Path(__file__).parents[2] / "runtimes/factor_execution/abi_fixture.py"
        ).read_text()
        assert code == expected
    elif phase == "output_format":
        assert json.loads(response.content)["output_format_decision"] is True
    elif phase == "final_decision":
        assert json.loads(response.content)["final_decision"] is True
    else:
        assert "Actual retained complete snapshot" in response.content
    assert len(router.submissions) == 1
    assert router.submissions[0]["controller_peer"] is router.peer
    assert (
        router.authorize.call_count == 3
    )  # Router, scoped call and physical-submit callback.


def test_embedding_shape_cardinality(router):
    router.call.kind = "embedding"
    result = router.handler(router.peer, "submit_embedding", body(True))
    response = RDEmbeddingResponse.model_validate_json(json.dumps(result["response"]))
    assert response.vectors == ((0.25, 0.75), (0.25, 0.75))


def test_unknown_chat_phase_cannot_submit(router):
    router.call.phase = "arbitrary"
    with pytest.raises(RPCError, match="fixed_chat_phase"):
        router.handler(router.peer, "submit_chat", body())
    assert not router.submissions


def test_authorization_failure_precedes_dispatch(router):
    router.authorize.side_effect = RPCError("dead_peer")
    with pytest.raises(RPCError, match="dead_peer"):
        router.handler(router.peer, "submit_chat", body())
    assert not router.submissions
