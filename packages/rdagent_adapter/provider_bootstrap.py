"""New model-backed controller bootstraps; no reinterpretation of fixture input."""

from typing import Literal
from uuid import UUID

from pydantic import model_validator

from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.provider_prompt import first_provider_messages
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent


class RDProviderOperationalBootstrap(RDOperationalBootstrap):
    schema_version: Literal["mineagent.rdagent-provider-operational-bootstrap.v1"] = (
        "mineagent.rdagent-provider-operational-bootstrap.v1"
    )
    config: ProviderFactorResearchConfig

    @model_validator(mode="after")
    def exact_steer_owner(self):
        if (
            self.config.controller_profile == "mineagent.rdagent-orchestrator.v4"
            and type(self) is not RDProviderSeededBootstrap
        ):
            raise ValueError(
                "Seeded controller requires exact retained parent bootstrap"
            )
        if self.config.steer_snapshot.owner_id != self.owner_id:
            raise ValueError("Frozen steer owner differs from controller owner")
        return self


class RDProviderSeededBootstrap(RDProviderOperationalBootstrap):
    schema_version: Literal["mineagent.rdagent-provider-seeded-bootstrap.v1"] = (
        "mineagent.rdagent-provider-seeded-bootstrap.v1"
    )
    continuation: Literal["seed_parent_first_generation"] = (
        "seed_parent_first_generation"
    )
    seeded_submission_id: UUID
    seed: ResolvedSeedParent

    @model_validator(mode="after")
    def exact_seed(self):
        if self.seed.reference.owner_id != self.owner_id:
            raise ValueError("Seed parent owner differs from controller owner")
        first_provider_messages(self.config, self.seed)
        return self


def build_operational_bootstrap(
    *, config, seed=None, seeded_submission_id=None, **scope
):
    """Data-only exact version selection; host/cold callers independently bind scope."""
    from packages.rdagent_adapter.provider_config import parse_research_config
    from packages.rdagent_adapter.seeded_bootstrap import RDSeededOperationalBootstrap

    config = parse_research_config(config)
    if (seed is None) != (seeded_submission_id is None):
        raise ValueError(
            "Seed receipt and exact retained seed must be supplied together"
        )
    configured = isinstance(config, ProviderFactorResearchConfig)
    if seed is None:
        cls = RDProviderOperationalBootstrap if configured else RDOperationalBootstrap
        return cls(config=config, **scope)
    cls = RDProviderSeededBootstrap if configured else RDSeededOperationalBootstrap
    return cls(
        config=config, seed=seed, seeded_submission_id=seeded_submission_id, **scope
    )
