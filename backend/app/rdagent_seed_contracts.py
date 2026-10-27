"""Explicit026 seed-parent protocol, never interpreted as a023 fresh request."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from backend.app.engine_run_submission_schemas import (
    PreflightReason,
    RDPreflight,
    RDSubmissionReceipt,
    RDSubmissionRequest,
    ReasonCode,
)
from packages.factors.contracts import Contract, Sha256
from packages.rdagent_adapter.seed_parent import SeedParentReference

CATALOG_REVISION = "configured_rd_seeded_single_output_session_v1"


class RDSeededSubmissionRequest(RDSubmissionRequest):
    schema_version: Literal["mineagent.rdagent-seeded-submission.v1"] = (
        "mineagent.rdagent-seeded-submission.v1"
    )
    parent_factor_version_id: UUID

    def research_declaration(self):
        """Read-only projection for shared data/workload checks, never a receipt."""
        return RDSubmissionRequest.model_validate(
            {
                **self.model_dump(
                    exclude={"schema_version", "parent_factor_version_id"}
                ),
                "schema_version": "mineagent.rdagent-submission.v1",
            }
        )


class RDSeededSubmit(RDSeededSubmissionRequest):
    preflight_sha256: Sha256

    def request(self):
        return RDSeededSubmissionRequest.model_validate(
            self.model_dump(exclude={"preflight_sha256"})
        )


class SeedPreflightReason(Contract):
    code: ReasonCode | Literal["seed_parent_unsupported", "seed_prompt_unsupported"]
    message: str = Field(min_length=1, max_length=320)

    @classmethod
    def shared(cls, value: PreflightReason):
        return cls.model_validate(value.model_dump())


class RDSeededPreflight(RDPreflight):
    schema_version: Literal["mineagent.rdagent-seeded-preflight.v1"] = (
        "mineagent.rdagent-seeded-preflight.v1"
    )
    normalized_request: RDSeededSubmissionRequest
    parent: SeedParentReference
    parent_binding_sha256: Sha256
    reasons: tuple[SeedPreflightReason, ...]
    runtime_profile: Literal["configured_rd_seeded_single_output_session_v1"] = (
        "configured_rd_seeded_single_output_session_v1"
    )


class RDSeededSubmissionReceipt(RDSubmissionReceipt):
    schema_version: Literal["mineagent.rdagent-seeded-submission-receipt.v1"] = (
        "mineagent.rdagent-seeded-submission-receipt.v1"
    )
    parent_factor_id: UUID
    parent_factor_version_id: UUID
    parent_binding_sha256: Sha256
