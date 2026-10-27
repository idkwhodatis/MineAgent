"""Closed model-backed envelope codec; historical engine fixture codec unchanged."""

import hashlib
import json

from pydantic import BaseModel

from packages.rdagent_provider.contracts import (
    RDProviderChatRequest,
    RDProviderChatResponse,
    RDProviderEmbeddingRequest,
    RDProviderEmbeddingResponse,
)
from packages.strategy_sdk import JsonLimits, parse_json

PROTOCOLS = frozenset(
    {
        "mineagent.rdagent-provider-chat.v1",
        "mineagent.rdagent-provider-embedding.v1",
    }
)


def _types(protocol):
    if protocol == "mineagent.rdagent-provider-chat.v1":
        return RDProviderChatRequest, RDProviderChatResponse
    if protocol == "mineagent.rdagent-provider-embedding.v1":
        return RDProviderEmbeddingRequest, RDProviderEmbeddingResponse
    raise ValueError("Unknown RD provider protocol")


def canonical_bytes(protocol, value):
    _types(protocol)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if len(raw) > 262144:
        raise ValueError("Provider envelope byte limit")
    return raw


def parse_request(protocol, value):
    cls, _ = _types(protocol)
    if type(value) is bytes:
        value = parse_json(
            value, limits=JsonLimits(max_bytes=262144, max_nodes=10000, max_items=256)
        )
    elif type(value) is not dict:
        raise ValueError("Exact provider request object/bytes required")
    request = cls.model_validate_json(canonical_bytes(protocol, value))
    if request.schema_version != protocol:
        raise ValueError("Provider protocol/version mismatch")
    return request


def validate_response(protocol, request, response):
    request_type, response_type = _types(protocol)
    if type(request) is not request_type or type(response) is not response_type:
        raise ValueError("Provider request/response type mismatch")
    request = parse_request(protocol, request.model_dump(mode="json"))
    response = response_type.model_validate_json(canonical_bytes(protocol, response))
    if request.kind == "embedding" and (
        len(response.vectors) != len(request.inputs)
        or any(len(vector) != request.dimensions for vector in response.vectors)
    ):
        raise ValueError("Embedding shape differs from exact request")
    return response


def request_sha256(protocol, request):
    return hashlib.sha256(
        canonical_bytes(
            protocol, parse_request(protocol, request.model_dump(mode="json"))
        )
    ).hexdigest()
