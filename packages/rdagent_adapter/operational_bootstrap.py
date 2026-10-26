"""Disabled operational controller input; distinct from compatibility probe setup.

No ticket, lease secret, provider key, arbitrary command or caller-selected path.
The supervisor supplies the authenticated IPC mount independently. This profile's
ONLY non-IPC input is /input/bootstrap.json, whose exact canonical bytes hash is
admission.input_sha256. No source/checkpoint/market-data mounts are permitted;
code is in the pinned image. Additional input requires a versioned complete input
inventory/profile, never reinterpretation of this bootstrap-only commitment.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes, digest
from packages.rdagent_adapter.contracts import FactorResearchConfig


class RDOperationalBootstrap(Contract):
    schema_version: Literal["mineagent.rdagent-operational-bootstrap.v1"] = (
        "mineagent.rdagent-operational-bootstrap.v1"
    )
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    spec_id: UUID
    attempt_binding_id: UUID
    config: FactorResearchConfig
    config_sha256: Sha256
    input_sha256: Sha256
    continuation: Literal["fresh_only"] = "fresh_only"
    input_layout: Literal["single_bootstrap_json_no_other_mounts"] = (
        "single_bootstrap_json_no_other_mounts"
    )

    @model_validator(mode="after")
    def exact(self):
        if (
            self.config.sha256 != self.config_sha256
            or self.config.input.sha256 != self.input_sha256
        ):
            raise ValueError(
                "Operational bootstrap differs from immutable specification"
            )
        if len(canonical_bytes(self)) > 262144:
            raise ValueError("Operational bootstrap exceeds fixed profile input cap")
        return self

    @property
    def sha256(self):
        return digest(self)
