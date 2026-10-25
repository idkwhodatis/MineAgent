"""Immutable native search inputs. No legacy config or executable callback payloads."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.broker_contracts import Contract, digest
from packages.research_runtime.budget_contracts import BudgetVector

SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UPSTREAM_SHA = "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"


class DevelopmentWindow(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    start: date
    end: date
    weight_millionths: int = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("Development window must have positive duration")
        return self


class ObjectiveProfile(Contract):
    schema_version: Literal["mineagent.objective-profile.v1"] = (
        "mineagent.objective-profile.v1"
    )
    name: Literal["native_annualized_return_minus_drawdown_pct.v1"] = (
        "native_annualized_return_minus_drawdown_pct.v1"
    )
    metric_paths: tuple[
        Literal["metrics.annualized_return", "metrics.max_drawdown"], ...
    ] = ("metrics.annualized_return", "metrics.max_drawdown")
    unit: Literal["percent"] = "percent"
    direction: Literal["maximize"] = "maximize"
    source_precision: Literal["unrounded_native_observation"] = (
        "unrounded_native_observation"
    )
    aggregation: Literal["normalized_weighted_arithmetic_mean"] = (
        "normalized_weighted_arithmetic_mean"
    )
    missing_policy: Literal["ineligible_null_score"] = "ineligible_null_score"
    windows: tuple[DevelopmentWindow, ...] = Field(min_length=1, max_length=8)
    minimum_closed_trades: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def exact_definition(self):
        if self.metric_paths != ("metrics.annualized_return", "metrics.max_drawdown"):
            raise ValueError("Objective metric order is immutable")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("Duplicate objective window")
        if sum(w.weight_millionths for w in self.windows) != 1_000_000:
            raise ValueError("Objective weights must sum to one million")
        return self


class SourceBinding(Contract):
    strategy_version_id: UUID
    source_sha256: SHA
    manifest_sha256: SHA


class InputFile(Contract):
    logical_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    sha256: SHA
    size_bytes: int = Field(ge=1, le=1_073_741_824)


class StrategyExecutionPlan(Contract):
    schema_version: Literal["mineagent.strategy-execution-plan.v1"] = (
        "mineagent.strategy-execution-plan.v1"
    )
    execution_mode: Literal["native"] = "native"
    result_policy: Literal["independent_observation.v1"] = "independent_observation.v1"
    trust: Literal["native_strategy_and_engine_share_process"] = (
        "native_strategy_and_engine_share_process"
    )
    dataset_version_id: UUID
    dataset_sha256: SHA
    files: tuple[InputFile, ...] = Field(min_length=1, max_length=32)
    universe_sha256: SHA
    listing_map_sha256: SHA
    calendar_sha256: SHA
    price_semantics_sha256: SHA
    engine_config_sha256: SHA
    parameter_sha256: SHA
    state_schema_sha256: SHA
    capture_profile_sha256: SHA
    evaluator_image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    market: Literal["CN", "US"]
    warmup_sessions: int = Field(ge=0, le=10000)
    rng_seed: int = Field(ge=0, le=2**32 - 1)
    windows: tuple[DevelopmentWindow, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_inventory(self):
        if len({f.logical_name for f in self.files}) != len(self.files):
            raise ValueError("Duplicate input file identity")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("Duplicate execution window")
        return self


class SeedPreparation(Contract):
    kind: Literal["none", "marker_parity_required"]
    original: SourceBinding
    prepared: SourceBinding
    transform_sha256: SHA | None = None
    parity_profile: Literal["native_marker_parity.v1"] | None = None

    @model_validator(mode="after")
    def exact_preparation(self):
        if self.kind == "none":
            if (
                self.original != self.prepared
                or self.transform_sha256 is not None
                or self.parity_profile is not None
            ):
                raise ValueError("Untransformed seed must retain exact identity")
        elif (
            self.original.strategy_version_id == self.prepared.strategy_version_id
            or self.original.source_sha256 == self.prepared.source_sha256
            or self.transform_sha256 is None
            or self.parity_profile is None
        ):
            raise ValueError(
                "Marker preparation needs a distinct version and parity input"
            )
        return self


class NoFactors(Contract):
    kind: Literal["none"] = "none"
    delivery: Literal["not_supported"] = "not_supported"


class PinnedFactors(Contract):
    kind: Literal["pinned"] = "pinned"
    delivery: Literal["not_supported"] = "not_supported"
    factor_run_binding_id: UUID
    binding_sha256: SHA
    exposure_sha256: SHA


class SearchSpec(Contract):
    schema_version: Literal["mineagent.strategy-search.v1"] = (
        "mineagent.strategy-search.v1"
    )
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    owner_id: UUID
    run_id: UUID
    preparation: SeedPreparation
    execution: StrategyExecutionPlan
    objective: ObjectiveProfile
    factors: Annotated[NoFactors | PinnedFactors, Field(discriminator="kind")]
    upstream_sha: Literal["8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc"] = UPSTREAM_SHA
    runtime_image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_lock_sha256: SHA
    adapter_patch_sha256: SHA
    prompt_profile_sha256: SHA
    budget: BudgetVector
    maximum_mutation_slots: int = Field(ge=1, le=32)
    population_size: int = Field(ge=1, le=64)
    archive_size: int = Field(ge=1, le=64)
    model_profile: Literal["openevolve-fixture.v1"] = "openevolve-fixture.v1"

    @model_validator(mode="after")
    def aligned(self):
        if self.execution.windows != self.objective.windows:
            raise ValueError("Objective and frozen execution windows differ")
        if self.maximum_mutation_slots > self.budget.mutation_slots:
            raise ValueError("Mutation slot authorization exceeds immutable budget")
        if any(
            getattr(self.budget, key) <= 0
            for key in (
                "calls",
                "tokens",
                "evaluator_executions",
                "wall_milliseconds",
                "storage_bytes",
                "process_slots",
            )
        ):
            raise ValueError(
                "Search requires nonzero execution and transport resources"
            )
        seed_evaluations = len(self.execution.windows) * (
            2 if self.preparation.kind == "marker_parity_required" else 1
        )
        if self.budget.evaluator_executions < seed_evaluations:
            raise ValueError("Budget cannot fund required seed preparation")
        return self

    @property
    def sha256(self):
        return digest(self)


class SearchOperationContext(Contract):
    """Proposed broker variant; not admitted by the neutral fixture parser."""

    schema_version: Literal["mineagent.openevolve-context.v1"] = (
        "mineagent.openevolve-context.v1"
    )
    owner_id: UUID
    run_id: UUID
    epoch_id: UUID
    iteration_intent_id: UUID
    native_iteration: int = Field(ge=1, le=1_000_000)
    purpose: Literal["mutation"] = "mutation"
    operation_ordinal: int = Field(ge=0, le=1_000_000)
    parent_candidate_id: UUID
    parent_source_sha256: SHA
    selection_snapshot_sha256: SHA
    rendered_context_sha256: SHA
