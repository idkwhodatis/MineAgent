"""Versioned public model binding and operator-only OpenAI-compatible declaration.

Unknown pricing is represented explicitly, never converted to zero. A binding is
immutable input, not provider authorization: each host call resolves the current
operator declaration and owner allowlist again. No credentials enter this model.
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import Contract, Sha256, digest

MODEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"


class PricingUnavailable(Contract):
    status: Literal["unavailable"] = "unavailable"


class DeclaredTokenPricing(Contract):
    status: Literal["operator_declared"] = "operator_declared"
    currency: Literal["USD"] = "USD"
    # Integer micro-USD per million tokens; explicit zero is an operator claim,
    # not inferred from missing usage/pricing or a synthetic transport.
    chat_input_microusd_per_million: int = Field(strict=True, ge=0, le=10**12)
    chat_output_microusd_per_million: int = Field(strict=True, ge=0, le=10**12)
    embedding_microusd_per_million: int = Field(strict=True, ge=0, le=10**12)


class RDModelBinding(Contract):
    schema_version: Literal["mineagent.rdagent-model-binding.v1"] = (
        "mineagent.rdagent-model-binding.v1"
    )
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    profile_sha256: Sha256
    transport: Literal["openai-compatible-chat-embedding.v1"] = (
        "openai-compatible-chat-embedding.v1"
    )
    chat_model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    embedding_model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    embedding_dimensions: int = Field(strict=True, ge=1, le=3072)
    max_output_tokens: int = Field(strict=True, ge=1, le=4096)
    request_deadline_ms: int = Field(strict=True, ge=1, le=8000)
    max_request_bytes: Literal[262144] = 262144
    max_response_bytes: Literal[262144] = 262144
    response_format: Literal["json_object_supported"] = "json_object_supported"
    pricing: DeclaredTokenPricing | PricingUnavailable
    synthetic: bool = Field(strict=True, default=False)

    @property
    def sha256(self):
        return digest(self)


class ProviderMessage(Contract):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class ProviderJSONFormat(Contract):
    type: Literal["json_object"] = "json_object"


class RDProviderChatRequest(Contract):
    schema_version: Literal["mineagent.rdagent-provider-chat.v1"] = (
        "mineagent.rdagent-provider-chat.v1"
    )
    kind: Literal["chat"] = "chat"
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_binding_sha256: Sha256
    model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    messages: tuple[ProviderMessage, ...] = Field(min_length=1, max_length=16)
    max_output_tokens: int = Field(strict=True, ge=1, le=4096)
    temperature_millionths: Literal[0] = 0
    response_format: ProviderJSONFormat | None

    @field_validator("temperature_millionths", mode="before")
    @classmethod
    def exact_temperature(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError("Exact zero integer temperature required")
        return value


class RDProviderEmbeddingRequest(Contract):
    schema_version: Literal["mineagent.rdagent-provider-embedding.v1"] = (
        "mineagent.rdagent-provider-embedding.v1"
    )
    kind: Literal["embedding"] = "embedding"
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_binding_sha256: Sha256
    model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    inputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    dimensions: int = Field(strict=True, ge=1, le=3072)

    @model_validator(mode="after")
    def bounded_inputs(self):
        if any(not value or len(value) > 8192 for value in self.inputs):
            raise ValueError("Embedding input limit")
        return self


def require_request_binding(request, binding):
    """Pure exact configuration agreement, not owner/network admission."""
    if (
        request.model_profile_id != binding.profile_id
        or request.model_binding_sha256 != binding.sha256
        or request.model
        != (binding.chat_model if request.kind == "chat" else binding.embedding_model)
        or (
            request.kind == "chat"
            and request.max_output_tokens > binding.max_output_tokens
        )
        or (
            request.kind == "embedding"
            and request.dimensions != binding.embedding_dimensions
        )
    ):
        raise ValueError("Provider request differs from immutable model binding")
    from packages.factors.contracts import canonical_bytes

    if len(canonical_bytes(request)) > binding.max_request_bytes:
        raise ValueError("Provider request envelope limit")
    return request


class RDProviderUsage(Contract):
    input_tokens: int | None = Field(strict=True, ge=0, le=10**8)
    output_tokens: int | None = Field(strict=True, ge=0, le=10**8)
    total_tokens: int | None = Field(strict=True, ge=0, le=10**8)
    provider_request_id: str | None = Field(default=None, max_length=256)
    usage_source: Literal["provider", "synthetic_provider"]
    billing_status: Literal["unknown"] = "unknown"

    @model_validator(mode="after")
    def exact_total(self):
        if self.provider_request_id is not None and any(
            ord(char) < 32 or ord(char) == 127 for char in self.provider_request_id
        ):
            raise ValueError("Provider identifier contains control characters")
        if (
            all(
                value is not None
                for value in (self.input_tokens, self.output_tokens, self.total_tokens)
            )
            and self.input_tokens + self.output_tokens != self.total_tokens
        ):
            raise ValueError("Provider token totals differ")
        return self


class RDProviderChatResponse(Contract):
    schema_version: Literal["mineagent.rdagent-provider-chat-response.v1"] = (
        "mineagent.rdagent-provider-chat-response.v1"
    )
    kind: Literal["chat"] = "chat"
    content: str = Field(min_length=1, max_length=32768)
    finish_reason: Literal["stop", "length"]
    usage: RDProviderUsage


class RDProviderEmbeddingResponse(Contract):
    schema_version: Literal["mineagent.rdagent-provider-embedding-response.v1"] = (
        "mineagent.rdagent-provider-embedding-response.v1"
    )
    kind: Literal["embedding"] = "embedding"
    vectors: tuple[tuple[float, ...], ...] = Field(min_length=1, max_length=16)
    usage: RDProviderUsage

    @field_validator("vectors", mode="before")
    @classmethod
    def finite_vectors(cls, value):
        import math

        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 16:
            raise ValueError("Embedding cardinality limit")
        for vector in value:
            if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 3072:
                raise ValueError("Embedding dimension limit")
            if any(
                type(item) not in (int, float) or not math.isfinite(item)
                for item in vector
            ):
                raise ValueError("Embedding values must be finite numbers")
        return value
