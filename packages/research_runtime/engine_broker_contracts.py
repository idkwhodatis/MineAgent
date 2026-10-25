"""Disabled engine-fixture codecs; never a widening of neutral fixture v1.

The activation revision must persist the exact protocol alongside request bytes,
typed context, immutable model profile and current-fence process authority. These
pure codecs do not register a model, allocate a budget or authorize transport.
"""

import hashlib
import json
import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .broker_contracts import Contract

Protocol = Literal[
    "mineagent.rdagent-chat.v1",
    "mineagent.rdagent-embedding.v1",
    "mineagent.openevolve-chat.v1",
]


def exact_integer(value):
    if type(value) is not int:
        raise ValueError("Numeric profile fields require exact integers")
    return value


class RDMessage(Contract):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class JSONFormat(Contract):
    type: Literal["json_object"]


class RDChatRequest(Contract):
    kind: Literal["chat"] = "chat"
    model: Literal["fixture.rdagent.v1"] = "fixture.rdagent.v1"
    messages: tuple[RDMessage, ...] = Field(min_length=1, max_length=16)
    max_output_tokens: int = Field(ge=1, le=4096)
    temperature_millionths: Literal[0] = 0
    # Required even when null: absence must not alias a differently parsed call.
    response_format: JSONFormat | None
    _integer = field_validator(
        "max_output_tokens", "temperature_millionths", mode="before"
    )(exact_integer)


class RDEmbeddingRequest(Contract):
    kind: Literal["embedding"] = "embedding"
    model: Literal["fixture.rdagent.v1"] = "fixture.rdagent.v1"
    inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    dimensions: Literal[2] = 2
    _integer = field_validator("dimensions", mode="before")(exact_integer)

    @model_validator(mode="after")
    def bounds(self):
        if any(not text or len(text) > 8192 for text in self.inputs):
            raise ValueError("Embedding input exceeds fixture profile")
        return self


class OEMessage(Contract):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=262144)


class OEChatRequest(Contract):
    kind: Literal["chat"] = "chat"
    model: Literal["openevolve-fixture.v1"] = "openevolve-fixture.v1"
    messages: tuple[OEMessage, OEMessage]
    max_output_tokens: Literal[32768] = 32768
    temperature_millionths: Literal[0] = 0
    _integer = field_validator(
        "max_output_tokens", "temperature_millionths", mode="before"
    )(exact_integer)

    @model_validator(mode="after")
    def role_order(self):
        if tuple(m.role for m in self.messages) != ("system", "user"):
            raise ValueError("Actual rendered system/user pair required")
        return self


class RDChatResponse(Contract):
    kind: Literal["chat"] = "chat"
    content: str = Field(min_length=1, max_length=32768)
    finish_reason: Literal["stop", "length"]


class RDEmbeddingResponse(Contract):
    kind: Literal["embedding"] = "embedding"
    vectors: tuple[tuple[float, float], ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def finite(self):
        if any(not math.isfinite(v) for row in self.vectors for v in row):
            raise ValueError("Embedding must contain finite fixture coordinates")
        return self


class OEChatResponse(Contract):
    kind: Literal["chat"] = "chat"
    content: str = Field(min_length=1, max_length=32768)

    @model_validator(mode="after")
    def byte_bound(self):
        if len(self.content.encode("utf-8")) > 32768 or "\0" in self.content:
            raise ValueError("Search response exceeds UTF-8 source profile")
        return self


_CODECS = {
    "mineagent.rdagent-chat.v1": (RDChatRequest, RDChatResponse, 262144),
    "mineagent.rdagent-embedding.v1": (RDEmbeddingRequest, RDEmbeddingResponse, 262144),
    "mineagent.openevolve-chat.v1": (OEChatRequest, OEChatResponse, 786432),
}


def _codec(protocol):
    if type(protocol) is not str or protocol not in _CODECS:
        raise ValueError("Explicit supported engine protocol required")
    return _CODECS[protocol]


def canonical_bytes(protocol: Protocol, value) -> bytes:
    _, _, limit = _codec(protocol)
    if isinstance(value, Contract):
        value = value.model_dump(mode="json")
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("Engine envelope exceeds its versioned byte bound")
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate engine envelope key")
        result[key] = value
    return result


def parse_request(protocol: Protocol, value: dict | bytes):
    cls, _, limit = _codec(protocol)
    if type(value) is bytes:
        if len(value) > limit:
            raise ValueError("Engine envelope exceeds its versioned byte bound")
        value = json.loads(value, object_pairs_hook=_pairs)
    elif type(value) is not dict:
        raise ValueError("Request must be a JSON object or exact JSON bytes")
    if type(value) is not dict:
        raise ValueError("Engine request root must be an object")
    # Reject bool-as-int even for Literal fields (Pydantic accepts True == 1).
    for key in ("max_output_tokens", "temperature_millionths", "dimensions"):
        if key in value and type(value[key]) is not int:
            raise ValueError("Numeric profile fields require exact integers")
    checked = cls.model_validate_json(canonical_bytes(protocol, value))
    canonical_bytes(protocol, checked)
    return checked


def validate_response(protocol: Protocol, request, response):
    request_type, response_type, _ = _codec(protocol)
    if type(request) is not request_type or type(response) is not response_type:
        raise ValueError("Response/request differs from explicit protocol")
    # Reparse rather than trusting objects constructed without validation.
    request = parse_request(protocol, request.model_dump(mode="json"))
    response = response_type.model_validate_json(canonical_bytes(protocol, response))
    if isinstance(request, RDEmbeddingRequest) and len(response.vectors) != len(
        request.inputs
    ):
        raise ValueError("Embedding response cardinality differs from input")
    return response


def request_sha256(protocol: Protocol, request) -> str:
    checked = parse_request(
        protocol,
        request.model_dump(mode="json") if isinstance(request, Contract) else request,
    )
    return hashlib.sha256(canonical_bytes(protocol, checked)).hexdigest()
