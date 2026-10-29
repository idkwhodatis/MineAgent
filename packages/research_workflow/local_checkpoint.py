"""Retained local normalization boundary facts, never a process/resume grant."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256
from packages.research_inputs.contracts import InputVersionRef


class LocalCheckpointObject(Contract):
    id: UUID
    kind: Literal["raw_external", "normalized_external"]
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=0, le=262144)


class LocalNormalizationCheckpoint(Contract):
    schema_version: Literal["mineagent.workflow-normalization-checkpoint.v1"] = (
        "mineagent.workflow-normalization-checkpoint.v1"
    )
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256
    allocation_sha256: Sha256
    derivation_id: UUID
    terminal_event_id: UUID
    terminal_event_sha256: Sha256
    state: Literal["succeeded", "rejected", "failed", "cancelled", "interrupted"]
    outputs: tuple[InputVersionRef, ...] = Field(max_length=32)
    objects: tuple[LocalCheckpointObject, ...] = Field(min_length=1, max_length=33)
    qualification: Literal[
        "local_publication_boundary_not_execution_settlement_or_launch_authority"
    ] = "local_publication_boundary_not_execution_settlement_or_launch_authority"

    @model_validator(mode="after")
    def exact_local_inventory(self):
        if (self.state == "succeeded") != bool(self.outputs):
            raise ValueError("Only actual successful normalization has output refs")
        if (
            len(self.objects) != 1 + len(self.outputs)
            or self.objects[0].kind != "raw_external"
        ):
            raise ValueError(
                "Exactly one existing raw object and ordered outputs required"
            )
        if len({obj.id for obj in self.objects}) != len(self.objects):
            raise ValueError("Duplicate retained object identity")
        if any(
            obj.kind != "normalized_external" or obj.sha256 != ref.content_sha256
            for obj, ref in zip(self.objects[1:], self.outputs, strict=True)
        ):
            raise ValueError("Normalized objects differ from their exact output refs")
        return self
