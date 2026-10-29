"""Distinct normal OE chat protocol; no RD/fixture alias and no launch authority.

Public bindings contain no endpoint or credential. Original rendered prompts and
provider responses must be retained by the durable host broker; these contracts
only validate their data. A provider-reported usage count is not a billing price.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import canonical_bytes, digest

from .candidate import Closed, _raw_fields
from .contracts import SHA

MODEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"


class ChatPricing(Closed):
    status: Literal["unavailable", "operator_declared"] = "unavailable"
    currency: Literal["USD"] = "USD"
    input_microusd_per_million: int | None = Field(default=None, ge=0, le=10**12)
    output_microusd_per_million: int | None = Field(default=None, ge=0, le=10**12)

    @model_validator(mode="after")
    def complete(self):
        rates = (self.input_microusd_per_million, self.output_microusd_per_million)
        if (self.status == "unavailable" and rates != (None, None)) or (
            self.status == "operator_declared"
            and any(type(rate) is not int for rate in rates)
        ):
            raise ValueError("Declared prices or explicit unknown required")
        return self


class OEChatBinding(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-binding.v1"] = (
        "mineagent.oe-structured-provider-binding.v1"
    )
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    profile_sha256: SHA
    model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    transport: Literal["openai-compatible-structured-chat.v1"] = (
        "openai-compatible-structured-chat.v1"
    )
    max_output_tokens: int = Field(ge=1, le=4096)
    request_deadline_ms: int = Field(ge=1, le=8000)
    max_request_bytes: Literal[262144] = 262144
    max_response_bytes: Literal[262144] = 262144
    pricing: ChatPricing = Field(default_factory=ChatPricing)
    synthetic: bool = False

    @field_validator("max_request_bytes", "max_response_bytes", mode="before")
    @classmethod
    def exact_byte_limits(cls, value):
        if type(value) is not int:
            raise ValueError("Exact integer byte limits required")
        return value

    @property
    def sha256(self):
        return digest(type(self).model_validate(_raw_fields(self)))


class PromptReference(Closed):
    role: Literal["previous_summary", "top", "diverse", "inspiration"]
    upstream_program_id: UUID
    candidate_sha256: SHA
    representation: Literal["summary_without_source_code", "source_code"]

    @model_validator(mode="after")
    def exact_representation(self):
        expected = (
            "summary_without_source_code"
            if self.role == "previous_summary"
            else "source_code"
        )
        if self.representation != expected:
            raise ValueError("Rendered reference representation differs")
        return self


class RenderedPrompt(Closed):
    """Exact existing upstream observer projection, including references/artifacts."""

    system: str = Field(min_length=1, max_length=131072)
    user: str = Field(min_length=1, max_length=131072)
    references: tuple[PromptReference, ...] = Field(max_length=16)
    current_candidate_sha256: SHA
    artifact_section_sha256: SHA | None

    @model_validator(mode="after")
    def bounded(self):
        if len(canonical_bytes(self)) > 262144:
            raise ValueError("Rendered provider prompt byte bound")
        return self


class ProviderMessage(Closed):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=131072)


class JSONObject(Closed):
    type: Literal["json_object"] = "json_object"


class OEChatRequest(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-chat.v1"] = (
        "mineagent.oe-structured-provider-chat.v1"
    )
    kind: Literal["chat"] = "chat"
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_binding_sha256: SHA
    model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    context_sha256: SHA
    candidate_domain_sha256: SHA
    parent_candidate_sha256: SHA
    execution_binding_sha256: SHA
    rendered_context_sha256: SHA
    messages: tuple[ProviderMessage, ProviderMessage]
    max_output_tokens: int = Field(ge=1, le=4096)
    temperature_millionths: Literal[0] = 0
    response_format: JSONObject

    @field_validator("temperature_millionths", mode="before")
    @classmethod
    def exact_temperature(cls, value):
        if type(value) is not int:
            raise ValueError("Exact integer temperature required")
        return value

    @model_validator(mode="after")
    def exact(self):
        if tuple(message.role for message in self.messages) != ("system", "user"):
            raise ValueError("Exact rendered system/user pair required")
        if type(self.temperature_millionths) is not int:
            raise ValueError("Exact zero temperature required")
        if len(canonical_bytes(self)) > 262144:
            raise ValueError("Provider request byte bound")
        return self

    @property
    def sha256(self):
        return digest(type(self).model_validate(_raw_fields(self)))


class OEChatResponse(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-response.v1"] = (
        "mineagent.oe-structured-provider-response.v1"
    )
    request_sha256: SHA
    content: str = Field(min_length=1, max_length=32768)
    finish_reason: Literal["stop", "length"]
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=256)
    input_tokens: int | None = Field(default=None, ge=0, le=100000000)
    output_tokens: int | None = Field(default=None, ge=0, le=100000000)
    total_tokens: int | None = Field(default=None, ge=0, le=100000000)
    usage_source: Literal["provider", "synthetic_provider"]
    billing_status: Literal["unknown"] = "unknown"

    @model_validator(mode="after")
    def bounded(self):
        if len(self.content.encode()) > 32768 or "\0" in self.content:
            raise ValueError("Provider candidate byte bound")
        if self.provider_request_id is not None and (
            len(self.provider_request_id.encode()) > 256
            or any(ord(c) < 32 for c in self.provider_request_id)
        ):
            raise ValueError("Provider response identifier bound")
        if all(
            v is not None
            for v in (self.input_tokens, self.output_tokens, self.total_tokens)
        ) and (self.input_tokens + self.output_tokens != self.total_tokens):
            raise ValueError("Provider usage totals differ")
        return self


def require_request_binding(request, binding):
    if type(request) is not OEChatRequest or type(binding) is not OEChatBinding:
        raise ValueError("Distinct OE provider protocol required")
    request = OEChatRequest.model_validate(_raw_fields(request))
    binding = OEChatBinding.model_validate(_raw_fields(binding))
    if (
        request.model_profile_id != binding.profile_id
        or request.model_binding_sha256 != binding.sha256
        or request.model != binding.model
        or request.max_output_tokens > binding.max_output_tokens
    ):
        raise ValueError("OE request differs from frozen operator model binding")
    return request
