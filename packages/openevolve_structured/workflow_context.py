"""Pure frozen workflow evidence for the controller, without host run schemas."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import canonical_bytes
from packages.research_workflow.model_inputs import ModelStageInputSnapshot

from .candidate import Closed
from .contracts import SHA


class WorkflowOEContext(Closed):
    schema_version: Literal["mineagent.workflow-oe-context.v1"] = (
        "mineagent.workflow-oe-context.v1"
    )
    definition_sha256: SHA
    stage_kind: Literal["structured_factor_execution"] = "structured_factor_execution"
    child_run_id: UUID
    inputs: ModelStageInputSnapshot
    rd_stage_id: UUID
    rd_checkpoint_id: UUID
    rd_checkpoint_sha256: SHA
    source_run_id: UUID
    source_attempt_id: UUID
    source_generation: int = Field(strict=True, ge=1)
    snapshot_id: UUID
    snapshot_manifest_sha256: SHA
    factor_version_id: UUID
    analysis_id: UUID
    analysis_result_sha256: SHA
    release_exposure_id: UUID
    release_provenance_sha256: SHA
    qualification: Literal["selected_evidence_not_release_or_platform_instruction"] = (
        "selected_evidence_not_release_or_platform_instruction"
    )

    @model_validator(mode="after")
    def exact(self):
        if self.inputs.binding.definition_sha256 != self.definition_sha256:
            raise ValueError("Exact workflow definition commitment required")
        if self.rd_stage_id == self.inputs.binding.stage_id:
            raise ValueError("OE stage cannot be its own RD predecessor")
        if len(canonical_bytes(self)) > 65536:
            raise ValueError("Workflow OE context exceeds retained byte bound")
        return self
