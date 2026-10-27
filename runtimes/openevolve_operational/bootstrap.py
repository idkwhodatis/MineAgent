"""Closed initial-epoch bootstrap and exact two-file input commitment."""

import hashlib
import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import Contract, SearchSpec
from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
from packages.openevolve_adapter.runtime_profile import PROFILE as SEARCH_PROFILE
from runtimes.openevolve_operational.profile import (
    BOOTSTRAP_BYTES,
    SEED_BYTES,
    profile_sha256,
)
from runtimes.openevolve_operational.runtime_binding import NativeRuntimeBinding


class OperationalBootstrap(Contract):
    schema_version: Literal["mineagent.openevolve-operational-bootstrap.v1"] = (
        "mineagent.openevolve-operational-bootstrap.v1"
    )
    admission_id: UUID
    epoch_id: UUID
    spec: SearchSpec
    native_inputs: ResolvedNativeInputs
    native_runtime: NativeRuntimeBinding
    issued_at: datetime
    not_after: datetime
    physical_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def closed(self):
        if (
            self.issued_at.tzinfo is None
            or self.not_after.tzinfo is None
            or not 0 < (self.not_after - self.issued_at).total_seconds() <= 600
        ):
            raise ValueError("fixed bootstrap deadline")
        if (
            self.physical_profile_sha256 != profile_sha256()
            or self.runtime_profile_sha256 != SEARCH_PROFILE.sha256
            or self.config_sha256 != self.spec.sha256
            or self.adapter_patch_sha256 != self.spec.adapter_patch_sha256
        ):
            raise ValueError("fixed bootstrap profile")
        if (
            self.spec.preparation.kind != "none"
            or self.spec.factors.kind != "none"
            or not 1 <= self.spec.maximum_mutation_slots <= 2
            or self.spec.population_size > 8
            or self.spec.archive_size > 8
        ):
            raise ValueError("initial fixed search only")
        from packages.openevolve_adapter.operational_profile import (
            require_initial_profile,
        )

        require_initial_profile(self.spec, self.native_inputs)
        if (
            self.native_runtime.protected_image_id
            != self.spec.execution.evaluator_image
        ):
            raise ValueError("Exact protected evaluator image binding required")
        return self

    def remaining(self, now):
        if type(now) not in (float, int) or not math.isfinite(now):
            raise ValueError("host clock")
        remaining = self.not_after.timestamp() - now
        if not 0 < remaining <= 600:
            raise ValueError("expired bootstrap")
        return remaining


def input_inventory(bootstrap_raw, seed):
    if (
        type(bootstrap_raw) is not bytes
        or type(seed) is not bytes
        or not 0 < len(bootstrap_raw) <= BOOTSTRAP_BYTES
        or not 0 < len(seed) <= SEED_BYTES
    ):
        raise ValueError("bootstrap inventory bound")
    bootstrap = OperationalBootstrap.model_validate_json(bootstrap_raw)
    if (
        canonical(bootstrap.model_dump(mode="json")) != bootstrap_raw
        or hashlib.sha256(seed).hexdigest()
        != bootstrap.spec.preparation.prepared.source_sha256
    ):
        raise ValueError("bootstrap inventory bytes")
    values = {
        "schema_version": "mineagent.openevolve-bootstrap-inventory.v1",
        "files": [
            {
                "destination": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
            for name, raw in (
                ("/bootstrap/bootstrap.json", bootstrap_raw),
                ("/bootstrap/seed.py", seed),
            )
        ],
    }
    return hashlib.sha256(canonical(values)).hexdigest(), values
