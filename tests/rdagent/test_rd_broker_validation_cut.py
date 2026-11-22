import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app import rdagent_provider_broker as broker
from packages.rdagent_provider.process_receipts import SDKIntent
from tests.integration import rd_broker_validation_cut as cut


def _intent(**changes):
    with pytest.raises(ValidationError) as raised:
        SDKIntent(
            **{
                "owner_id": uuid4(),
                "run_id": uuid4(),
                "attempt_id": uuid4(),
                "generation": 1,
                "transport_id": uuid4(),
                "request_sha256": "a" * 64,
                "model_binding_sha256": "b" * 64,
                "admitted_monotonic_ns": 10,
                "deadline_monotonic_ns": 9,
                "maximum_duration_ms": 1,
                "boot_id": uuid4(),
                **changes,
            }
        )
    return raised.value


def test_pydantic_intent_root_and_single_field_are_closed():
    assert cut._field(_intent()) == {
        "validation_field": "model",
        "validation_type": "value_error",
    }
    evidence = cut._field(_intent(deadline_monotonic_ns="private"))
    assert evidence == {
        "validation_field": "deadline_monotonic_ns",
        "validation_type": "int_type",
    }
    assert "private" not in json.dumps(evidence)
    assert (
        cut._field(_intent(deadline_monotonic_ns="private", owner_id="private")) == {}
    )
    assert cut._field(_intent(deadline_monotonic_ns=11, private_canary="private")) == {}


def _trace(code, line, next_trace=None):
    return NS(tb_frame=NS(f_code=code), tb_lineno=line, tb_next=next_trace)


def test_exact_source_and_code_are_required(monkeypatch, tmp_path):
    code = broker.submit_provider_request.__code__
    assert cut._site(_trace(code, 242)) == "sdk_intent"
    assert cut._site(_trace(code, 304)) == "embedding_response"
    assert cut._site(_trace(code.replace(), 242)) is None
    assert cut._site(_trace(code, 242, _trace(code, 242))) is None
    changed = tmp_path / "changed.py"
    changed.write_text("# private source")
    monkeypatch.setattr(broker, "__file__", str(changed))
    assert cut._site(_trace(code, 242)) is None


@pytest.mark.parametrize(
    "protocol",
    ["mineagent.rdagent-provider-chat.v1", "mineagent.rdagent-provider-embedding.v1"],
)
def test_real_sdk_intent_boundary_when_original_deadline_reaches_admission(
    monkeypatch, protocol
):
    from contextlib import nullcontext

    binding = NS(request_deadline_ms=1, sha256="b" * 64)
    operator = NS(connection=NS(secret_env="MINEAGENT_TEST_VALIDATION_CUT_KEY"))
    monkeypatch.setenv(operator.connection.secret_env, "synthetic-fixture")
    monkeypatch.setattr(
        broker, "time", NS(monotonic=lambda: 8, monotonic_ns=lambda: 10_000_000_000)
    )
    monkeypatch.setattr(
        broker, "parse_request", lambda *_: NS(model_profile_id="fixture")
    )
    monkeypatch.setattr(broker, "canonical_bytes", lambda *_: b"private")
    monkeypatch.setattr(
        broker,
        "neutral_transaction",
        lambda *_, **__: nullcontext(NS(scalar=lambda *_: None)),
    )
    monkeypatch.setattr(broker, "_publish", lambda *_: uuid4())
    monkeypatch.setattr(broker, "_authorize", lambda *_: None)
    monkeypatch.setattr(
        broker,
        "_scope",
        lambda f: {
            "owner_id": f.owner_id,
            "run_id": f.run_id,
            "attempt_id": f.attempt_id,
            "generation": 1,
        },
    )
    monkeypatch.setattr(
        broker, "require_research_spec", lambda *_: (None, NS(model_binding=binding))
    )
    monkeypatch.setattr(broker, "require_request_binding", lambda *_: None)
    monkeypatch.setattr(
        broker, "resolve_provider_declaration", lambda *_: (operator, binding)
    )
    monkeypatch.setattr(broker, "resolve_provider_runtime", lambda *_: None)
    monkeypatch.setattr(broker, "request_sha256", lambda *_: "a" * 64)
    monkeypatch.setattr(
        broker,
        "admit_atomic_request",
        lambda *_, **__: (NS(id=uuid4()), None, NS(id=uuid4())),
    )
    monkeypatch.setattr(broker, "mark_submitted", lambda *_: None)
    monkeypatch.setattr(
        broker,
        "_retain_receipt",
        lambda *_: pytest.fail("SDK intent should not be retained"),
    )
    with pytest.raises(ValidationError) as raised:
        broker.submit_provider_request(
            object(),
            NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4()),
            call_id=uuid4(),
            idempotency_key=uuid4(),
            relation="initial",
            protocol=protocol,
            request_bytes=b"private",
            settings=NS(rdagent_provider_python="synthetic-fixture"),
            controller_peer=object(),
            original_deadline=9,
        )
    assert cut.broker_validation_cut(raised.value) == {
        "validation_boundary": "sdk_intent",
        "validation_field": "model",
        "validation_type": "value_error",
    }


def test_pinned_chat_response_site_and_closed_pydantic_field():
    from packages.rdagent_provider.contracts import (
        RDProviderChatResponse,
        RDProviderUsage,
    )

    code = broker.submit_provider_request.__code__
    assert cut._site(_trace(code, 300)) == "chat_response"
    assert cut._site(_trace(code, 304)) == "embedding_response"
    usage = RDProviderUsage(
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        provider_request_id="private-canary",
        usage_source="synthetic_provider",
    )
    with pytest.raises(ValidationError) as raised:
        RDProviderChatResponse(content="", finish_reason="stop", usage=usage)
    assert cut._field(raised.value) == {
        "validation_field": "content",
        "validation_type": "string_too_short",
    }
