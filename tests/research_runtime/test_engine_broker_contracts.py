"""Disabled versioned engine codecs must not broaden the neutral v1 grammar."""

import hashlib
import json

import pytest

from packages.research_runtime import broker_contracts as neutral
from packages.research_runtime import engine_broker_contracts as engine

RD = "mineagent.rdagent-chat.v1"
OE = "mineagent.openevolve-chat.v1"
EMBED = "mineagent.rdagent-embedding.v1"


def rd():
    return {
        "kind": "chat",
        "model": "fixture.rdagent.v1",
        "messages": [
            {"role": "system", "content": "Frozen task"},
            {"role": "user", "content": "Return code"},
        ],
        "max_output_tokens": 4096,
        "temperature_millionths": 0,
        "response_format": None,
    }


def oe():
    value = rd()
    value.pop("response_format")
    value.update(model="openevolve-fixture.v1", max_output_tokens=32768)
    return value


def test_search_request_hash_preserves_exact_client_bytes():
    value = oe()
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert engine.request_sha256(OE, value) == hashlib.sha256(raw).hexdigest()


def test_response_format_changes_request_identity_and_cannot_be_omitted():
    value = rd()
    before = engine.request_sha256(RD, value)
    value["response_format"] = {"type": "json_object"}
    assert before != engine.request_sha256(RD, value)
    value.pop("response_format")
    with pytest.raises(ValueError):
        engine.parse_request(RD, value)


@pytest.mark.parametrize("value", [True, False, "0", 0.0, None])
def test_numeric_coercion_rejected(value):
    request = oe()
    request["temperature_millionths"] = value
    with pytest.raises(ValueError):
        engine.parse_request(OE, request)


@pytest.mark.parametrize("raw", [b'{"kind":"chat","kind":"chat"}', b"[]", b"null"])
def test_duplicate_or_nonobject_json_rejected(raw):
    with pytest.raises(ValueError):
        engine.parse_request(RD, raw)


def test_large_search_grammar_is_not_neutral_or_rd_admission():
    value = oe()
    value["messages"][1]["content"] = "x" * 100000
    assert engine.parse_request(OE, value).messages[1].content == "x" * 100000
    for parser in (
        lambda: neutral.parse_request(value),
        lambda: engine.parse_request(RD, value),
    ):
        with pytest.raises(ValueError):
            parser()


def test_unicode_byte_cap_not_only_character_count():
    value = oe()
    value["messages"][1]["content"] = "😀" * 200000
    with pytest.raises(ValueError):
        engine.parse_request(OE, value)
    with pytest.raises(ValueError):
        engine.OEChatResponse(content="😀" * 10000)


def test_exact_system_user_order_and_no_assistant_search_fallback():
    value = oe()
    value["messages"].reverse()
    with pytest.raises(ValueError):
        engine.parse_request(OE, value)


def test_rd_continuation_finish_reason_retained():
    request = engine.parse_request(RD, rd())
    response = engine.RDChatResponse(content='{"code":', finish_reason="length")
    assert engine.validate_response(RD, request, response).finish_reason == "length"
    with pytest.raises(ValueError):
        engine.validate_response(OE, request, response)


@pytest.mark.parametrize(
    "vectors", [[[True, 1.0]], [[float("nan"), 1.0]], [[1.0]], [[1.0, 2.0, 3.0]]]
)
def test_embedding_rejects_coercion_nonfinite_and_wrong_dimension(vectors):
    with pytest.raises(ValueError):
        engine.RDEmbeddingResponse(vectors=tuple(tuple(row) for row in vectors))


def test_embedding_requires_exact_input_output_membership():
    request = engine.parse_request(EMBED, {"inputs": ["a", "b"]})
    response = engine.RDEmbeddingResponse(vectors=((1.0, 0.0),))
    with pytest.raises(ValueError):
        engine.validate_response(EMBED, request, response)


def test_constructed_invalid_response_is_revalidated():
    request = engine.parse_request(RD, rd())
    forged = engine.RDChatResponse.model_construct(content="x", finish_reason="unknown")
    with pytest.raises(ValueError):
        engine.validate_response(RD, request, forged)


def test_unknown_version_is_never_a_neutral_fallback():
    with pytest.raises(ValueError):
        engine.parse_request("mineagent.rdagent-chat.v2", rd())
