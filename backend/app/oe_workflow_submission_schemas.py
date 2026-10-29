"""Internal workflow-origin request; no route accepts this as a human action."""

from typing import Literal

from pydantic import model_validator

from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from packages.openevolve_structured.workflow_run import WorkflowOEContext


class WorkflowStructuredSearchRequest(ProviderStructuredSearchRequest):
    schema_version: Literal["mineagent.oe-structured-workflow-submission.v1"] = (
        "mineagent.oe-structured-workflow-submission.v1"
    )
    profile: Literal["workflow_fixed_factor_operator_closed_program_v1"] = (
        "workflow_fixed_factor_operator_closed_program_v1"
    )
    workflow_context: WorkflowOEContext

    @model_validator(mode="after")
    def actual_source_binding(self):
        if self.snapshot_id != self.workflow_context.snapshot_id:
            raise ValueError("Workflow request must use its exact predecessor snapshot")
        return self


from uuid import UUID

from packages.factors.contracts import Contract, Sha256


class WorkflowStructuredSubmissionReceipt(Contract):
    schema_version: Literal[
        "mineagent.oe-structured-workflow-submission-receipt.v1"
    ] = "mineagent.oe-structured-workflow-submission-receipt.v1"
    id: UUID
    run_id: UUID
    workflow_id: UUID
    stage_id: UUID
    origin: Literal["workflow_stage"] = "workflow_stage"
    request_sha256: Sha256
    preflight_sha256: Sha256
    workflow_context_sha256: Sha256
