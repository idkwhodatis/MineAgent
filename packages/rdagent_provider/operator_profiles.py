"""Host-only operator profile; never installed in the isolated RD controller."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import Contract, digest
from packages.rdagent_provider.contracts import (
    MODEL_NAME,
    DeclaredTokenPricing,
    PricingUnavailable,
    RDModelBinding,
)
from packages.tradegrad_adapter.model_profiles import OperatorModelProfile


class RDOperatorModelProfile(Contract):
    """Server configuration only; never a public submission payload."""

    schema_version: Literal["mineagent.rdagent-operator-model.v1"] = (
        "mineagent.rdagent-operator-model.v1"
    )
    connection: OperatorModelProfile
    embedding_model: str = Field(min_length=1, max_length=160, pattern=MODEL_NAME)
    embedding_dimensions: int = Field(strict=True, ge=1, le=3072)
    supports_json_object: Literal[True] = True
    pricing: DeclaredTokenPricing | PricingUnavailable = Field(
        default_factory=PricingUnavailable
    )

    @field_validator("supports_json_object", mode="before")
    @classmethod
    def exact_support(cls, value):
        if value is not True:
            raise ValueError("Explicit JSON-object support required")
        return value

    @model_validator(mode="after")
    def fixed_limits(self):
        connection = self.connection
        if (
            connection.temperature != 0
            or connection.max_output_tokens > 4096
            or connection.request_timeout_seconds > 8
            or connection.max_request_bytes != 262144
            or connection.max_response_bytes != 262144
        ):
            raise ValueError(
                "RD model declaration exceeds fixed first-iteration bounds"
            )
        return self

    @property
    def sha256(self):
        # Includes endpoint/secret ENV NAME/allowlist in a non-reversible binding,
        # never the secret value. Do not return the operator declaration publicly.
        return digest(self)

    def public_binding(self, owner_id):
        if type(owner_id) is not UUID or owner_id not in self.connection.owner_ids:
            raise ValueError("RD model profile is not allowed for this owner")
        return RDModelBinding(
            profile_id=self.connection.id,
            profile_sha256=self.sha256,
            chat_model=self.connection.model,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
            max_output_tokens=self.connection.max_output_tokens,
            request_deadline_ms=int(self.connection.request_timeout_seconds * 1000),
            pricing=self.pricing,
            synthetic=self.connection.synthetic,
        )
