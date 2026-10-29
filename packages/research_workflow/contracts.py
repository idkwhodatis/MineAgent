"""Closed control commands, separating selection from actual consumption.

A workflow checkpoint never resumes an old native unit. It permits a new stage
only after the retained terminal graph is independently revalidated. Exposure
and sealed-evaluation eligibility remain separate mandatory gates.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import (
    Contract,
    Sha256,
    aware_utc,
    canonical_bytes,
    digest,
)
from packages.research_inputs.contracts import InputVersionRef, SourceOutputRef

StageKind = Literal[
    "external_normalization",
    "experiment_feedback_projection",
    "rd_factor_generation",
    "structured_factor_execution",
]
WorkflowMode = Literal["continuous", "pause_after_stage"]
WorkflowStatus = Literal[
    "ready",
    "running",
    "pause_requested",
    "paused",
    "cancel_requested",
    "cancelled",
    "blocked",
    "completed",
]
ConsumptionState = Literal[
    "not_observed",
    "selected_not_submitted",
    "included_in_submitted_prompt",
    "submission_uncertain",
    "not_consumed_numeric",
    "not_submitted_to_model",
]


class WorkflowCommand(Contract):
    schema_version: Literal["mineagent.workflow-command.v1"] = (
        "mineagent.workflow-command.v1"
    )
    workflow_id: UUID
    expected_revision: int = Field(strict=True, ge=1, le=1_000_000)
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    action: Literal["pause_after_stage", "cancel", "resume"]


class WorkflowCommandReceipt(Contract):
    schema_version: Literal["mineagent.workflow-command-receipt.v1"] = (
        "mineagent.workflow-command-receipt.v1"
    )
    workflow_id: UUID
    command_id: UUID
    command_sha256: Sha256
    revision: int = Field(strict=True, ge=1, le=1_000_000)
    status: WorkflowStatus
    # Requested pause/cancel is not physical stop or a safe checkpoint.
    checkpoint_id: UUID | None
    current_stage_id: UUID | None


class StageConsumption(Contract):
    """Read projection, never accepted as execution/stop authority."""

    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    state: ConsumptionState
    model_operation_id: UUID | None
    request_sha256: Sha256 | None
    transport_id: UUID | None = None

    @model_validator(mode="after")
    def exact_consumption_evidence(self):
        attempted = self.state in {
            "included_in_submitted_prompt",
            "submission_uncertain",
        }
        if any(
            attempted != (value is not None)
            for value in (
                self.model_operation_id,
                self.transport_id,
                self.request_sha256,
            )
        ):
            raise ValueError(
                "Only an actual submitted model request can claim inclusion or uncertain submission"
            )
        return self


class StageExecutionBinding(Contract):
    """Must be part of the NEW child submission/bootstrap, not post-hoc metadata."""

    schema_version: Literal["mineagent.workflow-stage-binding.v1"] = (
        "mineagent.workflow-stage-binding.v1"
    )
    submission_origin: Literal["workflow_stage"] = "workflow_stage"
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256


class StageTerminalReference(Contract):
    """Audit reference only. Resume rechecks the real retained terminal graph."""

    schema_version: Literal["mineagent.workflow-stage-terminal.v1"] = (
        "mineagent.workflow-stage-terminal.v1"
    )
    binding: StageExecutionBinding
    child_run_id: UUID
    child_request_sha256: Sha256
    terminal_status: Literal["succeeded", "failed", "cancelled", "interrupted"]
    # Exact inventory commitment, not a caller-supplied all_stopped boolean.
    process_receipts_sha256: Sha256
    budget_settlements_sha256: Sha256


class WorkflowInputSnapshot(Contract):
    schema_version: Literal["mineagent.workflow-input-snapshot.v1"] = (
        "mineagent.workflow-input-snapshot.v1"
    )
    owner_id: UUID
    workflow_id: UUID
    revision_id: UUID
    refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def exact_bounded_selection(self):
        if any(ref.origin == "unknown" for ref in self.refs):
            raise ValueError("Unknown provenance cannot be selected for a new stage")
        roots = {(ref.origin, ref.record_id) for ref in self.refs}
        versions = {(ref.origin, ref.version_id) for ref in self.refs}
        if len(roots) != len(self.refs) or len(versions) != len(self.refs):
            raise ValueError("Select one immutable version per input record")
        if len(canonical_bytes(self)) > 32768:
            raise ValueError("Workflow input snapshot exceeds its byte limit")
        return self

    @property
    def sha256(self):
        return digest(self)


class WorkflowControls(Contract):
    pause_after_stage: bool = Field(strict=True, default=False)
    cancel: bool = Field(strict=True, default=False)
    resume: bool = Field(strict=True, default=False)
    reason_code: str = Field(min_length=1, max_length=80)


class WorkflowStageView(Contract):
    stage_id: UUID
    ordinal: int = Field(strict=True, ge=1, le=256)
    kind: StageKind
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256
    child_run_id: UUID | None
    consumption: ConsumptionState
    # Only IDs/hashes here; result content still uses its existing release gate.
    checkpoint_id: UUID | None

    @model_validator(mode="after")
    def exact_stage_family(self):
        local = self.kind in {
            "external_normalization",
            "experiment_feedback_projection",
        }
        if local != (self.child_run_id is None):
            raise ValueError("Only a real engine stage references a child run")
        expected = "not_submitted_to_model" if local else "not_consumed_numeric"
        if self.kind != "rd_factor_generation" and self.consumption != expected:
            raise ValueError(
                "Deterministic stages cannot claim model guidance consumption"
            )
        if self.kind == "rd_factor_generation" and self.consumption not in {
            "not_observed",
            "selected_not_submitted",
            "included_in_submitted_prompt",
            "submission_uncertain",
        }:
            raise ValueError("Model stage requires explicit submitted-request state")
        return self


class WorkflowView(Contract):
    schema_version: Literal["mineagent.workflow-view.v1"] = "mineagent.workflow-view.v1"
    workflow_id: UUID
    revision: int = Field(strict=True, ge=1, le=1_000_000)
    mode: WorkflowMode
    status: WorkflowStatus
    selected_input_revision_id: UUID
    current_stage: WorkflowStageView | None
    latest_checkpoint_id: UUID | None
    blocked_reason: str | None = Field(default=None, max_length=80)
    controls: WorkflowControls
    qualification: Literal["stage_metadata_not_result_release_or_process_authority"] = (
        "stage_metadata_not_result_release_or_process_authority"
    )


class WorkflowStageHistory(WorkflowStageView):
    """Actual source-record projection; unknown is not inferred running/success."""

    source_status: Literal[
        "not_recorded",
        "prepared",
        "started_no_terminal_receipt",
        "queued",
        "preparing",
        "running",
        "finalizing",
        "pause_requested",
        "paused",
        "cancel_requested",
        "succeeded",
        "rejected",
        "failed",
        "cancelled",
        "interrupted",
        "blocked",
    ]
    child_attempt_id: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    input_refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)
    derived_input_refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)
    automatic_feedback_refs: tuple[InputVersionRef, ...] = Field(
        default=(), max_length=1
    )
    predecessor_checkpoint_id: UUID | None = None
    output_refs: tuple[SourceOutputRef, ...] = Field(default=(), max_length=32)
    model_requests: tuple[StageConsumption, ...] = Field(default=(), max_length=1024)
    containment: Literal["not_applicable", "not_observed", "positive_stop", "uncertain"]
    settlement: Literal["not_observed", "settled", "unsettled", "uncertain"]
    qualification: Literal[
        "retained_stage_metadata_not_live_status_or_result_release"
    ] = "retained_stage_metadata_not_live_status_or_result_release"

    @field_validator("started_at", "finished_at")
    @classmethod
    def exact_utc_times(cls, value):
        return None if value is None else aware_utc(value)

    @model_validator(mode="after")
    def exact_history_scope(self):
        if self.automatic_feedback_refs and (
            self.predecessor_checkpoint_id is None
            or any(
                ref.origin != "experiment_feedback_proposal"
                or ref not in self.input_refs
                for ref in self.automatic_feedback_refs
            )
        ):
            raise ValueError(
                "Automatic feedback must bind the actual predecessor and frozen inputs"
            )
        if any(
            (row.stage_id, row.input_revision_id, row.input_sha256)
            != (self.stage_id, self.input_revision_id, self.input_sha256)
            for row in self.model_requests
        ):
            raise ValueError("Model request history differs from frozen stage input")
        if self.child_run_id is None and self.child_attempt_id is not None:
            raise ValueError("Local processing cannot invent a child execution attempt")
        if any(
            row.stage_id != self.stage_id or row.child_run_id != self.child_run_id
            for row in self.output_refs
        ):
            raise ValueError("Output history differs from its actual stage/run")
        if (
            self.finished_at is not None
            and self.started_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("Stage timestamps are reversed")
        return self
