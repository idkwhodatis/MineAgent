"""Public normal027 metadata projection; no DB/runtime imports or launch grant."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSubmissionReceipt,
)
from backend.app.schemas import RunStatus
from packages.openevolve_structured.candidate import Closed
from packages.openevolve_structured.provider_contracts import OEChatBinding
from packages.research_runtime.budget_contracts import BudgetVector


class StructuredRunActions(Closed):
    cancel: bool = False
    pause: Literal[False] = False
    resume: Literal[False] = False
    reason_code: Literal[
        "structured_worker_control_not_registered",
        "structured_pause_resume_not_supported",
        "cancel_pending_containment",
    ] = "structured_worker_control_not_registered"


class ProviderStructuredRunView(Closed):
    schema_version: Literal["mineagent.oe-structured-run-view.v1"] = (
        "mineagent.oe-structured-run-view.v1"
    )
    id: UUID
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    status: RunStatus
    generation: int = Field(ge=1, le=1)
    active_attempt_id: UUID | None
    created_at: datetime
    updated_at: datetime
    submission: ProviderStructuredSubmissionReceipt
    factor_version_id: UUID
    dataset_version_id: UUID
    model_binding: OEChatBinding
    budget_limits: BudgetVector
    budget_used: BudgetVector
    budget_reserved: BudgetVector
    # Unit count is declared cumulative work, not proof any unit ran or stopped.
    declared_process_units: int = Field(ge=4, le=6)
    data_qualification: Literal[
        "synthetic_by_construction", "user_declaration_unverified"
    ] = "synthetic_by_construction"
    qualification: Literal["fixed_snapshot_comparison_not_causal_gain"] = (
        "fixed_snapshot_comparison_not_causal_gain"
    )
    actions: StructuredRunActions = Field(default_factory=StructuredRunActions)


class StructuredControlReceipt(Closed):
    schema_version: Literal["mineagent.oe-structured-control-receipt.v1"] = (
        "mineagent.oe-structured-control-receipt.v1"
    )
    run_id: UUID
    status: RunStatus
    pending: bool


class WorkflowStructuredRunOrigin(Closed):
    """Verified relational metadata, not a human submission acknowledgement."""

    schema_version: Literal["mineagent.oe-workflow-run-origin.v1"] = (
        "mineagent.oe-workflow-run-origin.v1"
    )
    id: UUID
    run_id: UUID
    owner_id: UUID
    origin: Literal["workflow_stage"] = "workflow_stage"
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    snapshot_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_process_units: int = Field(ge=4, le=6)


class WorkflowStructuredRunView(ProviderStructuredRunView):
    schema_version: Literal["mineagent.oe-structured-run-view.v2"] = (
        "mineagent.oe-structured-run-view.v2"
    )
    submission: WorkflowStructuredRunOrigin
