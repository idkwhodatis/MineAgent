"""Exact resolved content for a new model stage, never caller execution authority.

The host constructs this only after fresh source-owner/version/release checks.
The new child config must retain both this full content commitment and the stage
binding. Historical 026 manual-steer config is not reinterpreted as this type.
"""

from typing import Literal

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, canonical_bytes, digest
from packages.research_inputs.resolved import ResolvedInputVersion
from packages.research_workflow.contracts import (
    StageExecutionBinding,
    WorkflowInputSnapshot,
)


class ModelStageInputSnapshot(Contract):
    schema_version: Literal["mineagent.workflow-model-inputs.v1"] = (
        "mineagent.workflow-model-inputs.v1"
    )
    binding: StageExecutionBinding
    selection: WorkflowInputSnapshot
    inputs: tuple[ResolvedInputVersion, ...] = Field(default=(), max_length=32)
    qualification: Literal["resolved_data_not_platform_instruction_or_approval"] = (
        "resolved_data_not_platform_instruction_or_approval"
    )

    @model_validator(mode="after")
    def exact_bound_selection(self):
        bound, selected = self.binding, self.selection
        if (
            bound.owner_id,
            bound.workflow_id,
            bound.input_revision_id,
            bound.input_sha256,
        ) != (
            selected.owner_id,
            selected.workflow_id,
            selected.revision_id,
            selected.sha256,
        ):
            raise ValueError("Resolved model input differs from frozen stage selection")
        if tuple(value.ref for value in self.inputs) != selected.refs:
            raise ValueError(
                "Resolved model inputs must preserve every selected version and order"
            )
        if len(canonical_bytes(self)) > 32768:
            raise ValueError("Combined model input exceeds the immutable byte bound")
        return self

    @property
    def sha256(self):
        return digest(self)
