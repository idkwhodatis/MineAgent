"""Metadata-only engine history. No execution grants or legacy result coercion."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.schemas import RunStatus
from packages.research_runtime.budget_contracts import BudgetVector


class RDModelProfileOption(BaseModel):
    id: str
    model_binding_sha256: str
    chat_model: str
    embedding_model: str
    synthetic: bool
    pricing_status: Literal["unavailable", "operator_declared"]
    billing_status: Literal["unknown"] = "unknown"
    recommended_budget: BudgetVector | None
    seeded_recommended_budget: BudgetVector | None = None
    seeded_can_create: bool = False
    seeded_reason_code: str = "seeded_runtime_unavailable"
    can_create: bool
    reason_code: str


class EngineCapability(BaseModel):
    purpose: Literal["factor_research", "strategy_search"]
    engine: Literal["rdagent", "openevolve"]
    can_read_history: bool
    can_preflight: bool = False
    recommended_budget: BudgetVector | None = None
    max_output_sessions: int | None = None
    max_iterations: int | None = None
    model_profile: Literal["fixture.rdagent.v1"] | None = None
    can_create: bool = False
    configured_model_can_create: bool = False
    configured_model_reason_code: str = "rd_model_unconfigured"
    model_profiles: tuple[RDModelProfileOption, ...] = ()
    max_selected_steers: Literal[8] = 8
    reason_code: str


class EngineCapabilities(BaseModel):
    engines: list[EngineCapability]


class DisabledActions(BaseModel):
    cancel: bool = False
    pause: Literal[False] = False
    resume: Literal[False] = False
    reason_code: str = "engine_control_not_enabled"


class EngineRunSummary(BaseModel):
    id: UUID
    purpose: Literal["factor_research"] = "factor_research"
    engine: Literal["rdagent"] = "rdagent"
    config_schema: Literal[
        "mineagent.factor-research.v1",
        "mineagent.factor-research.v2",
        "mineagent.factor-research.v3",
    ] = "mineagent.factor-research.v1"
    status: RunStatus
    generation: int
    dataset_version_id: UUID
    strategy_version_id: None = None
    task_name: str
    created_at: datetime
    updated_at: datetime
    actions: DisabledActions = Field(default_factory=DisabledActions)


class EngineRunDetail(EngineRunSummary):
    config_sha256: str
    task_description: str
    max_iterations: int
    # Limits are declarations, not observed usage or remaining execution credit.
    budget_limits: dict[str, int]
    error: str | None
    evidence_scope: Literal["persisted_metadata_not_execution_acceptance"] = (
        "persisted_metadata_not_execution_acceptance"
    )


class EngineHistoryItem(BaseModel):
    id: UUID
    attempt_id: UUID
    generation: int
    logical_step: int
    created_at: datetime
    source_id: UUID | None
    source_sha256: str | None
    factor_version_id: UUID | None
    factor_id: UUID | None
    generation_outcome: str | None
    error_code: str | None
    evaluated_source_id: UUID | None
    adopted_source_id: UUID | None
    continuation_source_id: UUID | None
    evaluation_id: UUID | None
    evaluation_status: str | None
    snapshot_id: UUID | None
    snapshot_input_sha256: str | None
    snapshot_output_sha256: str | None

    evaluation_protocol: Literal["legacy_result", "execution_result"] | None = None
    execution_snapshot_publication_id: UUID | None = None
    execution_snapshot_id: UUID | None = None
    execution_snapshot_schema: (
        Literal["mineagent.factor-execution-snapshot.v1"] | None
    ) = None
    execution_snapshot_state: Literal["publication_pending", "completed"] | None = None
    execution_snapshot_input_sha256: str | None = None
    execution_snapshot_output_sha256: str | None = None
    execution_snapshot_manifest_sha256: str | None = None
