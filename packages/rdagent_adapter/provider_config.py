"""Exact configured-model successor; historical fixture config v1 stays strict."""

from typing import Literal

from pydantic import model_validator

from packages.factors.contracts import Sha256
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.rdagent_provider.contracts import RDModelBinding


class ProviderFactorResearchConfig(FactorResearchConfig):
    schema_version: Literal["mineagent.factor-research.v2"] = (
        "mineagent.factor-research.v2"
    )
    model_profile: Literal["configured.rdagent.v1"] = "configured.rdagent.v1"
    model_binding: RDModelBinding
    steer_snapshot: SteerSnapshot
    steer_snapshot_sha256: Sha256

    @model_validator(mode="after")
    def exact_steer(self):
        if (
            self.controller_profile is not None
            and self.budget.wall_milliseconds
            < 300000 + 32 * self.model_binding.request_deadline_ms
        ):
            raise ValueError(
                "Composed provider budget must cover unchanged model-call bounds"
            )
        if self.steer_snapshot.sha256 != self.steer_snapshot_sha256:
            raise ValueError("Frozen steer snapshot hash differs")
        return self


def parse_research_config(value):
    if isinstance(value, (FactorResearchConfig, ProviderFactorResearchConfig)):
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        raise ValueError("Exact research config object required")
    version = value.get("schema_version")
    if version == "mineagent.factor-research.v1":
        return FactorResearchConfig.model_validate(value)
    if version == "mineagent.factor-research.v2":
        return ProviderFactorResearchConfig.model_validate(value)
    raise ValueError("Unknown research configuration version")
