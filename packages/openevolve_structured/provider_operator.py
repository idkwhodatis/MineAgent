"""Host-only OE operator routing; never copied into the search controller image."""

from typing import Literal
from uuid import UUID

from pydantic import field_validator, model_validator

from packages.factors.contracts import digest
from packages.tradegrad_adapter.model_profiles import OperatorModelProfile

from .candidate import Closed, _raw_fields
from .provider_contracts import ChatPricing, OEChatBinding


class OEOperatorChatProfile(Closed):
    schema_version: Literal["mineagent.oe-structured-operator-chat.v1"] = (
        "mineagent.oe-structured-operator-chat.v1"
    )
    connection: OperatorModelProfile
    pricing: ChatPricing
    supports_json_object: Literal[True] = True

    @field_validator("supports_json_object", mode="before")
    @classmethod
    def exact_support(cls, value):
        if value is not True:
            raise ValueError("Explicit JSON-object support required")
        return value

    @field_validator("connection", mode="before")
    @classmethod
    def strict_connection_primitives(cls, value):
        raw = _raw_fields(value)
        if type(raw) is not dict:
            raise ValueError("Exact operator connection required")
        for key in ("max_output_tokens", "max_request_bytes", "max_response_bytes"):
            if key in raw and type(raw[key]) is not int:
                raise ValueError("Exact operator integer limits required")
        for key in ("temperature", "request_timeout_seconds"):
            if key in raw and type(raw[key]) not in (int, float):
                raise ValueError("Exact operator numeric limits required")
        if "synthetic" in raw and type(raw["synthetic"]) is not bool:
            raise ValueError("Exact operator synthetic qualification required")
        return OperatorModelProfile.model_validate(raw)

    @model_validator(mode="after")
    def bounded(self):
        value = self.connection
        if (
            value.temperature != 0
            or value.max_output_tokens > 4096
            or value.request_timeout_seconds > 8
            or value.max_request_bytes != 262144
            or value.max_response_bytes != 262144
        ):
            raise ValueError(
                "OE provider profile exceeds admitted initial transport limits"
            )
        return self

    def public_binding(self, owner_id):
        """Configuration agreement only; host must still authorize each call."""
        exact = type(self).model_validate(_raw_fields(self))
        if type(owner_id) is not UUID or owner_id not in exact.connection.owner_ids:
            raise ValueError("Operator profile is unavailable to this owner")
        return OEChatBinding(
            profile_id=exact.connection.id,
            profile_sha256=digest(exact),
            model=exact.connection.model,
            max_output_tokens=exact.connection.max_output_tokens,
            request_deadline_ms=int(exact.connection.request_timeout_seconds * 1000),
            pricing=exact.pricing,
            synthetic=exact.connection.synthetic,
        )
