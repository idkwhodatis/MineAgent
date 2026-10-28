"""Versioned, immutable input contracts for the disabled RD-Agent admission slice.

These types confer no execution authority. The control plane must bind them to
its same-owner run/spec, current attempt fence, process and budget registries.
"""

from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import (
    CalculationUniverse,
    Contract,
    DatasetBinding,
    DateWindow,
    Identifier,
    InputField,
    NoPreprocessing,
    Preprocessing,
    Sha256,
    digest,
)
from packages.rdagent_adapter.controller_profile import ControllerProfileSelection
from packages.research_runtime.budget_contracts import BudgetVector

RDAGENT_COMMIT = "484776c211e4fbbeef03e0ec00d6bbee7362a4f4"
QLIB_COMMIT = "54355232463878d2eebb91fe0ee5fa7fa1f5976c"
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
BoundedText = Annotated[
    str, Field(min_length=1, max_length=8000, pattern=r"^[^\x00]+$")
]


class DependencyLock(Contract):
    role: Literal["orchestrator", "candidate", "normalizer", "analysis"]
    sha256: Sha256


class RuntimePins(Contract):
    rdagent_commit: Literal["484776c211e4fbbeef03e0ec00d6bbee7362a4f4"] = RDAGENT_COMMIT
    qlib_commit: Literal["54355232463878d2eebb91fe0ee5fa7fa1f5976c"] = QLIB_COMMIT
    adapter_version: Literal["mineagent.rdagent.v1"] = "mineagent.rdagent.v1"
    dependency_locks: tuple[DependencyLock, ...] = Field(min_length=4, max_length=4)
    orchestrator_image: ImageDigest
    candidate_image: ImageDigest
    normalizer_image: ImageDigest
    analysis_image: ImageDigest
    candidate_profile: Literal["mineagent.factor-prefix-candidate.v2"] = (
        "mineagent.factor-prefix-candidate.v2"
    )
    normalizer_profile: Literal["mineagent.factor-numeric-hdf.v1"] = (
        "mineagent.factor-numeric-hdf.v1"
    )

    @model_validator(mode="after")
    def locks(self):
        if [x.role for x in self.dependency_locks] != [
            "analysis",
            "candidate",
            "normalizer",
            "orchestrator",
        ]:
            raise ValueError("Every runtime requires its own ordered dependency lock")
        return self


class FactorTaskSpec(Contract):
    name: Identifier
    description: BoundedText
    formulation: BoundedText
    explanation_origin: Literal["task_declaration"] = "task_declaration"
    factor_kind: Literal["cross_sectional", "time_series"]
    output_unit: Identifier
    interpretation: Literal["higher_preferred", "lower_preferred", "unsigned"]


class FrozenResearchInput(Contract):
    dataset: DatasetBinding
    calculation_universe: CalculationUniverse
    window: DateWindow
    calendar_sha256: Sha256
    market_timezone: Identifier
    input_fields: tuple[InputField, ...] = Field(min_length=1, max_length=5)
    lookback_sessions: int = Field(strict=True, ge=1, le=63)
    history_semantics: Literal["prior_sessions_plus_current"] = (
        "prior_sessions_plus_current"
    )
    lag_sessions: int = Field(strict=True, ge=0, le=8)
    preprocessing: Preprocessing = Field(default_factory=NoPreprocessing)
    membership_policy: Literal["static_available_by_every_cutoff"] = (
        "static_available_by_every_cutoff"
    )
    dependency_claim: Literal["provided_not_proven_read"] = "provided_not_proven_read"

    @field_validator("market_timezone")
    @classmethod
    def timezone(cls, value):
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def declared_fields(self):
        names = [item.name for item in self.input_fields]
        if len(names) != len(set(names)) or set(names) - {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            raise ValueError(
                "Only distinct frozen OHLCV fields may enter factor research"
            )
        prices = {
            item.price_semantics
            for item in self.input_fields
            if item.price_semantics != "not_price"
        }
        if prices and prices != {self.dataset.price_semantics}:
            raise ValueError("Research input price semantics differ from dataset")
        return self

    @property
    def sha256(self):
        return digest(self)


class FactorResearchConfig(ControllerProfileSelection):
    schema_version: Literal["mineagent.factor-research.v1"] = (
        "mineagent.factor-research.v1"
    )
    input: FrozenResearchInput
    task: FactorTaskSpec
    runtime: RuntimePins
    budget: BudgetVector
    max_iterations: int = Field(strict=True, ge=1, le=8)
    model_profile: Literal["fixture.rdagent.v1"] = "fixture.rdagent.v1"
    upstream_workers: Literal[1] = 1
    operation: Literal["factor_implementation_refinement"] = (
        "factor_implementation_refinement"
    )
    max_source_bytes: Literal[131072] = 131072
    max_output_rows_per_cutoff: int = Field(strict=True, ge=1, le=10_000)
    max_output_bytes_per_cutoff: int = Field(strict=True, ge=1024, le=16_777_216)

    @field_validator("upstream_workers", "max_source_bytes", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Runtime limits require exact integers")
        return value

    @model_validator(mode="after")
    def bounded_budget(self):
        if self.controller_profile is not None:
            if self.max_iterations != 1:
                raise ValueError("Composed controller profile requires one iteration")
            if self.budget.wall_milliseconds < 300000:
                raise ValueError(
                    "Composed controller budget must cover controller and three children"
                )
        if self.budget.calls < 1 or self.budget.mutation_slots < self.max_iterations:
            raise ValueError("Research requires model and iteration reservations")
        if (
            min(
                self.budget.evaluator_executions,
                self.budget.wall_milliseconds,
                self.budget.storage_bytes,
                self.budget.process_slots,
            )
            < 1
        ):
            raise ValueError("Research requires bounded execution resources")
        if (
            len(self.input.calculation_universe.listing_ids)
            > self.max_output_rows_per_cutoff
        ):
            raise ValueError(
                "Output cap cannot represent the full calculation universe"
            )
        return self

    @property
    def sha256(self):
        return digest(self)
