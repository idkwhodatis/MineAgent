"""Actual deterministic feedback boundary, not a model-consumption grant."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256
from packages.research_inputs.contracts import InputVersionRef, SourceOutputRef


class FeedbackCheckpointObject(Contract):
    id: UUID
    kind: Literal["normalized_feedback"] = "normalized_feedback"
    sha256: Sha256
    size_bytes: int = Field(strict=True, gt=0, le=32768)


class FeedbackCheckpoint(Contract):
    schema_version: Literal["mineagent.workflow-feedback-checkpoint.v1"] = (
        "mineagent.workflow-feedback-checkpoint.v1"
    )
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256
    allocation_sha256: Sha256
    rd_checkpoint_id: UUID
    source: SourceOutputRef
    derivation_id: UUID
    terminal_event_id: UUID
    terminal_event_sha256: Sha256
    state: Literal["succeeded", "rejected", "failed", "cancelled", "interrupted"]
    outputs: tuple[InputVersionRef, ...] = Field(max_length=1)
    objects: tuple[FeedbackCheckpointObject, ...] = Field(max_length=1)
    exposure_ids: tuple[UUID, ...] = Field(max_length=2)
    qualification: Literal[
        "released_development_projection_not_model_consumption_or_launch"
    ] = "released_development_projection_not_model_consumption_or_launch"

    @model_validator(mode="after")
    def exact(self):
        if (
            self.source.workflow_id != self.workflow_id
            or self.source.output_kind != "direct_analysis"
        ):
            raise ValueError("Exact same-workflow direct analysis required")
        if self.state == "succeeded":
            if (
                len(self.outputs) != 1
                or len(self.objects) != 1
                or len(self.exposure_ids) != 2
                or len(set(self.exposure_ids)) != 2
            ):
                raise ValueError("One actual released feedback output required")
            if (
                self.outputs[0].origin != "experiment_feedback_proposal"
                or self.outputs[0].content_sha256 != self.objects[0].sha256
            ):
                raise ValueError("Actual feedback object commitment differs")
        elif self.outputs or self.objects or self.exposure_ids:
            raise ValueError("Unsuccessful feedback cannot invent output or release")
        return self
