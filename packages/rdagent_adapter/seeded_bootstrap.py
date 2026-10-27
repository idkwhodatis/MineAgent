"""Explicit026 single-file seeded bootstrap; old v1 grammar remains unchanged.

Parent source is data inside the bounded canonical bootstrap, not an extra mount,
command, previous successful knowledge or inherited execution result. The exact
whole-file input hash binds it to the independently authenticated controller.
"""

import json
from typing import Literal
from uuid import UUID

from pydantic import model_validator

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.seed_prompt_budget import first_prompt_messages


class RDSeededOperationalBootstrap(RDOperationalBootstrap):
    schema_version: Literal["mineagent.rdagent-seeded-operational-bootstrap.v1"] = (
        "mineagent.rdagent-seeded-operational-bootstrap.v1"
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
        first_prompt_messages(self.config, self.seed)
        return self


def parse_bootstrap(raw):
    """Explicit canonical version dispatch; never silently reinterpret v1."""
    if type(raw) is not bytes or not 1 <= len(raw) <= 262144:
        raise ValueError("Bounded controller bootstrap bytes required")
    document = json.loads(raw)
    if type(document) is not dict:
        raise ValueError("Controller bootstrap object required")
    from packages.rdagent_adapter.provider_bootstrap import (
        RDProviderOperationalBootstrap,
        RDProviderSeededBootstrap,
    )

    version = document.get("schema_version")
    cls = {
        "mineagent.rdagent-operational-bootstrap.v1": RDOperationalBootstrap,
        "mineagent.rdagent-provider-operational-bootstrap.v1": RDProviderOperationalBootstrap,
        "mineagent.rdagent-provider-seeded-bootstrap.v1": RDProviderSeededBootstrap,
        "mineagent.rdagent-seeded-operational-bootstrap.v1": RDSeededOperationalBootstrap,
    }.get(version)
    if cls is None:
        raise ValueError("Unknown controller bootstrap version")
    value = cls.model_validate_json(raw)
    if canonical_bytes(value) != raw:
        raise ValueError("Canonical duplicate-free bootstrap required")
    return value
