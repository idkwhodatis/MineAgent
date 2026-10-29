"""Configured RD submission/preflight grammar; no client-selected runtime authority."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.schemas import RunStatus
from packages.factors.contracts import Contract, DateWindow, Sha256, digest
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.rdagent_adapter.contracts import FactorTaskSpec
from packages.research_runtime.budget_contracts import BudgetVector


class RDSubmissionRequest(Contract):
    schema_version: Literal["mineagent.rdagent-submission.v1"] = (
        "mineagent.rdagent-submission.v1"
    )
    purpose: Literal["factor_research"] = "factor_research"
    engine: Literal["rdagent"] = "rdagent"
    dataset_version_id: UUID
    window: DateWindow
    input_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...] = Field(
        min_length=1, max_length=5
    )
    lookback_sessions: int = Field(strict=True, ge=1, le=63)
    lag_sessions: int = Field(strict=True, ge=0, le=8)
    task: FactorTaskSpec
    objective: DirectObjectiveDeclaration
    budget: BudgetVector
    max_iterations: int = Field(strict=True, ge=1, le=8)
    source_policy: ConfiguredSourcePolicy

    @model_validator(mode="after")
    def exact_fields(self):
        if len(set(self.input_fields)) != len(self.input_fields):
            raise ValueError("Input fields must be distinct and ordered")
        if self.objective.window != self.window:
            raise ValueError(
                "Initial objective covers the exact declared output window"
            )
        return self

    @property
    def sha256(self):
        return digest(self)


class RDSubmit(RDSubmissionRequest):
    preflight_sha256: Sha256

    def request(self):
        return RDSubmissionRequest.model_validate(
            self.model_dump(exclude={"preflight_sha256"})
        )


ReasonCode = Literal[
    "runtime_disabled",
    "runtime_unconfigured",
    "runtime_unsupported",
    "task_profile_unsupported",
    "runtime_schema_unavailable",
    "dataset_format_unsupported",
    "dataset_bytes_unavailable",
    "source_projection_unsupported",
    "output_window_unsupported",
    "insufficient_history",
    "insufficient_label_horizon",
    "delayed_output_unsupported",
    "budget_below_profile",
    "objective_coverage_impossible",
]


class PreflightReason(Contract):
    code: ReasonCode
    message: str = Field(min_length=1, max_length=320)


class RDPreflight(Contract):
    schema_version: Literal["mineagent.rdagent-preflight.v1"] = (
        "mineagent.rdagent-preflight.v1"
    )
    normalized_request: RDSubmissionRequest
    request_sha256: Sha256
    preflight_sha256: Sha256
    dataset_content_sha256: Sha256
    frozen_input_sha256: Sha256 | None
    runtime_profile_sha256: Sha256 | None = None
    data_eligible: bool
    can_submit: bool
    reasons: tuple[PreflightReason, ...]
    cutoff_count: int = Field(strict=True, ge=0, le=4096)
    required_process_units: int = Field(strict=True, ge=0, le=65545)
    output_window: DateWindow
    lookback_semantics: Literal["prior_sessions_plus_current"] = (
        "prior_sessions_plus_current"
    )
    model_profile: Literal["fixture.rdagent.v1"] = "fixture.rdagent.v1"
    runtime_profile: Literal["configured_rd_single_output_session_v1"] = (
        "configured_rd_single_output_session_v1"
    )
    qualification: Literal["declared_source_not_verified_pit_or_out_of_sample"] = (
        "declared_source_not_verified_pit_or_out_of_sample"
    )


class EngineDatasetOption(Contract):
    source: str | None = None
    id: UUID
    name: str
    start_date: date
    end_date: date
    market: str
    is_synthetic: bool
    content_sha256: Sha256
    eligibility: Literal["not_checked", "unsupported"]
    reason: PreflightReason | None = None


class RDSubmissionReceipt(Contract):
    schema_version: Literal["mineagent.rdagent-submission-receipt.v1"] = (
        "mineagent.rdagent-submission-receipt.v1"
    )
    run_id: UUID
    plan_id: UUID
    request_sha256: Sha256
    preflight_sha256: Sha256
    accepted: Literal[True] = True
    replayed: bool
    status: RunStatus


class RDControlReceipt(Contract):
    schema_version: Literal["mineagent.rdagent-control-receipt.v1"] = (
        "mineagent.rdagent-control-receipt.v1"
    )
    run_id: UUID
    action: Literal["cancel"] = "cancel"
    status: RunStatus
    pending: bool
