"""027 normal-owner submission grammar; never caller-selected runtime authority.

Routing/schema registration is deliberately deferred until the new persisted
candidate/execution graph and real worker are available. Display pin state is
not part of immutable execution semantics or a proposal-approval requirement.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import DateWindow, digest
from packages.openevolve_structured.candidate import Closed, _raw_fields
from packages.openevolve_structured.contracts import SHA
from packages.openevolve_structured.objective import StructuredObjective
from packages.research_runtime.budget_contracts import BudgetVector
from packages.structured_factor_strategy.contracts import StructuredProgram


class StructuredSeedParameters(Closed):
    threshold: float = Field(strict=True, ge=-1e15, le=1e15)
    top_k: int = Field(strict=True, ge=1, le=100)
    maximum_weight: float = Field(strict=True, gt=0, le=1)
    maximum_gross_weight: float = Field(strict=True, gt=0, le=1)

    def program(self, signal_window):
        """The host obtains the signal window from the actual selected snapshot."""
        values = type(self).model_validate(_raw_fields(self))
        return StructuredProgram(signal_window=signal_window, **values.model_dump())


class StructuredSearchRequest(Closed):
    schema_version: Literal["mineagent.oe-structured-submission.v1"] = (
        "mineagent.oe-structured-submission.v1"
    )
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    profile: Literal["fixed_factor_closed_program_v1"] = (
        "fixed_factor_closed_program_v1"
    )
    snapshot_id: UUID
    seed_parameters: StructuredSeedParameters
    objective: StructuredObjective
    budget: BudgetVector
    maximum_mutation_slots: int = Field(ge=1, le=2)

    @field_validator("budget", mode="before")
    @classmethod
    def exact_integer_budget(cls, value):
        raw = _raw_fields(value)
        if type(raw) is not dict or any(type(v) is not int for v in raw.values()):
            raise ValueError("Budget dimensions require exact integers, never booleans")
        return raw

    @model_validator(mode="after")
    def revalidate_nested(self):
        StructuredSeedParameters.model_validate(_raw_fields(self.seed_parameters))
        StructuredObjective.model_validate(_raw_fields(self.objective))
        BudgetVector.model_validate(_raw_fields(self.budget))
        if (self.objective.window.end - self.objective.window.start).days > 8:
            raise ValueError("Initial fixed execution window is at most eight days")
        return self

    @property
    def sha256(self):
        normalized = type(self).model_validate(_raw_fields(self))
        return digest(normalized)


class StructuredSearchSubmit(StructuredSearchRequest):
    preflight_sha256: SHA

    def request(self):
        values = _raw_fields(self)
        values.pop("preflight_sha256")
        return StructuredSearchRequest.model_validate(values)


class StructuredPreflightReason(Closed):
    code: Literal[
        "runtime_disabled",
        "runtime_unconfigured",
        "runtime_schema_unavailable",
        "snapshot_not_completed",
        "snapshot_not_released",
        "source_data_unavailable",
        "market_profile_unsupported",
        "execution_window_unsupported",
        "budget_below_profile",
    ]
    message: str = Field(min_length=1, max_length=320)


class StructuredSearchPreflight(Closed):
    schema_version: Literal["mineagent.oe-structured-preflight.v1"] = (
        "mineagent.oe-structured-preflight.v1"
    )
    normalized_request: StructuredSearchRequest
    request_sha256: SHA
    preflight_sha256: SHA
    snapshot_manifest_sha256: SHA
    frozen_binding_sha256: SHA | None
    factor_version_id: UUID
    dataset_version_id: UUID
    signal_window: DateWindow
    required_budget: BudgetVector
    required_process_units: int = Field(ge=3, le=4)
    data_eligible: bool
    can_submit: bool
    reasons: tuple[StructuredPreflightReason, ...]
    model_profile: Literal["openevolve-structured-fixture.v1"] = (
        "openevolve-structured-fixture.v1"
    )
    qualification: Literal[
        "fixed_snapshot_comparison_not_verified_pit_or_causal_gain"
    ] = "fixed_snapshot_comparison_not_verified_pit_or_causal_gain"

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.request_sha256 != self.normalized_request.sha256
            or self.required_process_units
            != self.normalized_request.maximum_mutation_slots + 2
        ):
            raise ValueError(
                "Exact normalized request and controller-plus-evaluations required"
            )
        if self.can_submit and (
            not self.data_eligible or self.reasons or self.frozen_binding_sha256 is None
        ):
            raise ValueError("Submission requires complete eligible preflight")
        return self


class StructuredSubmissionReceipt(Closed):
    schema_version: Literal["mineagent.oe-structured-submission-receipt.v1"] = (
        "mineagent.oe-structured-submission-receipt.v1"
    )
    id: UUID
    run_id: UUID
    request_sha256: SHA
    preflight_sha256: SHA
    normalized_request: StructuredSearchRequest
    origin: Literal["authenticated_user_submission"] = "authenticated_user_submission"
    # This identifies the authenticated submission, not authorship of a preset
    # or a claim that each later model proposal received human approval.
    origin_user_id: UUID

    @model_validator(mode="after")
    def exact_request(self):
        if self.request_sha256 != self.normalized_request.sha256:
            raise ValueError("Submission origin must retain exact original request")
        return self
