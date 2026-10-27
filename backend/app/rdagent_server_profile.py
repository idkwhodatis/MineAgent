"""Operator-only fixed initial RD profile; never accepted from a user request.

Configuration is an explicit activation decision, not an assertion that an image
has passed CI. The installation must first complete the documented real gate.
Launchers still independently inspect exact image/source/dependency inventories.
"""

from typing import Literal

from pydantic import model_validator

from packages.factors.contracts import Contract
from packages.rdagent_adapter.contracts import ImageDigest, RuntimePins
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
)

ROLE_LOCKS = {
    "orchestrator": "3bedebb0bf60663d69f0e3e35d0b18be94bb69639b6a8d069d6366360f33009c",
    "candidate": CANDIDATE_LOCK_SHA256,
    "normalizer": NORMALIZER_LOCK_SHA256,
    "analysis": "33cefc2eba90e996a848e0f8a92789654102364446d5bc290c94fb85f473a228",
}


class RDServerRuntimeProfile(Contract):
    schema_version: Literal["mineagent.rdagent-server-profile.v1"] = (
        "mineagent.rdagent-server-profile.v1"
    )
    profile: Literal["configured_rd_single_output_session_v1"] = (
        "configured_rd_single_output_session_v1"
    )
    model_profile: Literal["fixture.rdagent.v1"] = "fixture.rdagent.v1"
    runtime: RuntimePins
    qlib_installed_parent_image_id: ImageDigest

    @model_validator(mode="after")
    def exact_dependencies(self):
        if {
            item.role: item.sha256 for item in self.runtime.dependency_locks
        } != ROLE_LOCKS:
            raise ValueError(
                "Server profile requires the exact four shipped dependency locks"
            )
        return self
