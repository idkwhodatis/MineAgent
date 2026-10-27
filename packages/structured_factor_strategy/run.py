"""Explicit structured research declaration; no native/portable policy alias."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, DateWindow, Sha256
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.research_runtime.budget_contracts import BudgetVector


class StructuredRunConfig(Contract):
    schema_version: Literal["mineagent.structured-factor-run.v1"] = (
        "mineagent.structured-factor-run.v1"
    )
    structured_strategy_version_id: UUID
    program_sha256: Sha256
    dataset_version_id: UUID
    dataset_sha256: Sha256
    snapshots: tuple[UUID, ...] = Field(min_length=1, max_length=2)
    execution_window: DateWindow
    initial_cash: float = Field(strict=True, gt=0, le=1e9)
    source_policy: ConfiguredSourcePolicy
    engine_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    engine_installed_parent_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    engine_dependency_lock_sha256: Sha256
    budget: BudgetVector
    replay_policy: Literal["initial_attempt_only_no_process_replay"] = (
        "initial_attempt_only_no_process_replay"
    )
    purpose: Literal["factor_strategy"] = "factor_strategy"
    engine: Literal["structured"] = "structured"

    @model_validator(mode="after")
    def bounded_units(self):
        if len(set(self.snapshots)) != len(self.snapshots):
            raise ValueError("Duplicate selected snapshot")
        count = len(self.snapshots)
        if not self.budget.covers(
            BudgetVector(
                process_slots=count,
                evaluator_executions=count,
                wall_milliseconds=90000 * count,
                storage_bytes=268500992 * count,
            )
        ):
            raise ValueError(
                "Structured declared budget cannot cover conservative units"
            )
        if (self.execution_window.end - self.execution_window.start).days > 8:
            raise ValueError("Initial structured window exceeds eight days")
        return self
