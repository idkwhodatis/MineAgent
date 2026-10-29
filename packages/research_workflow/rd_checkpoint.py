"""Immutable successful child boundary references, never release/resume authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256


class RDStageCheckpoint(Contract):
    schema_version: Literal["mineagent.workflow-rd-checkpoint.v1"] = (
        "mineagent.workflow-rd-checkpoint.v1"
    )
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256
    allocation_sha256: Sha256
    submission_id: UUID
    child_run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    spec_id: UUID
    plan_id: UUID
    start_id: UUID
    source_id: UUID
    evaluation_id: UUID
    factor_version_id: UUID
    source_sha256: Sha256
    snapshot_id: UUID
    snapshot_manifest_sha256: Sha256
    analysis_id: UUID
    analysis_result_sha256: Sha256
    analysis_measurement_sha256: Sha256
    controller_admission_id: UUID
    controller_stop_sha256: Sha256
    process_admission_ids: tuple[UUID, ...] = Field(min_length=4, max_length=18)
    reservation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=256)
    qualification: Literal[
        "completed_owned_child_not_result_release_or_next_launch"
    ] = "completed_owned_child_not_result_release_or_next_launch"

    @model_validator(mode="after")
    def exact_references(self):
        if (
            len(set(self.process_admission_ids)) != len(self.process_admission_ids)
            or len(set(self.reservation_ids)) != len(self.reservation_ids)
            or self.controller_admission_id not in self.process_admission_ids
        ):
            raise ValueError("Child boundary reference inventory differs")
        return self
