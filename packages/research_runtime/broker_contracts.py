"""Closed neutral fixture envelopes; real engine contexts activate separately."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Message(Contract):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class ChatRequest(Contract):
    kind: Literal["chat"] = "chat"
    model: Literal["neutral-fixture-v1"] = "neutral-fixture-v1"
    messages: tuple[Message, ...] = Field(min_length=1, max_length=16)
    max_output_tokens: int = Field(ge=1, le=4096)
    temperature_millionths: int = Field(default=0, ge=0, le=1000000)


class EmbeddingRequest(Contract):
    kind: Literal["embedding"] = "embedding"
    model: Literal["neutral-fixture-v1"] = "neutral-fixture-v1"
    inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    dimensions: int = Field(ge=1, le=64)


Request = Annotated[ChatRequest | EmbeddingRequest, Field(discriminator="kind")]
REQUEST = TypeAdapter(Request)


class FixtureContext(Contract):
    schema_version: Literal["mineagent.neutral-fixture-context.v1"] = (
        "mineagent.neutral-fixture-context.v1"
    )
    scenario: Literal["chat", "embedding", "retry", "continuation", "cancellation"]
    ordinal: int = Field(ge=0, le=1000000)


class ObjectRef(Contract):
    id: UUID
    kind: Literal["model_request", "model_response"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChatResponse(Contract):
    kind: Literal["chat"] = "chat"
    content: str = Field(min_length=1, max_length=32768)


class EmbeddingResponse(Contract):
    kind: Literal["embedding"] = "embedding"
    vectors: tuple[tuple[float, ...], ...] = Field(min_length=1, max_length=16)


Response = Annotated[ChatResponse | EmbeddingResponse, Field(discriminator="kind")]
RESPONSE = TypeAdapter(Response)


def canonical_bytes(value: BaseModel | dict) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    result = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    if len(result) > 262144:
        raise ValueError("Broker envelope exceeds byte bound")
    return result


def digest(value: BaseModel | dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_request(value: dict) -> ChatRequest | EmbeddingRequest:
    # JSON mode preserves strict scalar validation while accepting JSON arrays.
    request = REQUEST.validate_json(canonical_bytes(value))
    if isinstance(request, EmbeddingRequest) and any(
        not x or len(x) > 8192 for x in request.inputs
    ):
        raise ValueError("Embedding input exceeds bound")
    return request


def validate_response(request, response) -> None:
    if request.kind != response.kind:
        raise ValueError("Response kind does not match request")
    if isinstance(request, EmbeddingRequest) and (
        len(response.vectors) != len(request.inputs)
        or any(len(v) != request.dimensions for v in response.vectors)
    ):
        raise ValueError("Embedding shape does not match request")
    canonical_bytes(response)
