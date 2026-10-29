"""Normal027 operator-backed submissions, distinct from the sealed fixture ABI.

An authenticated request selects public operator metadata, never an endpoint,
secret, image or process grant. No route is registered by importing this module.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.app.oe_structured_submission_schemas import StructuredSeedParameters
from packages.factors.contracts import digest
from packages.openevolve_structured.candidate import Closed, _raw_fields
from packages.openevolve_structured.contracts import SHA
from packages.openevolve_structured.objective import StructuredObjective
from packages.openevolve_structured.provider_contracts import OEChatBinding
from packages.research_runtime.budget_contracts import BudgetVector


class ProviderStructuredSearchRequest(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-submission.v1"] = (
        "mineagent.oe-structured-provider-submission.v1"
    )
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    profile: Literal["fixed_factor_operator_closed_program_v1"] = (
        "fixed_factor_operator_closed_program_v1"
    )
    snapshot_id: UUID
    seed_parameters: StructuredSeedParameters
    objective: StructuredObjective
    budget: BudgetVector
    maximum_mutation_slots: int = Field(ge=1, le=2)
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_binding_sha256: SHA

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def canonical_http_uuid(cls, value):
        # FastAPI validates object JSON in Python mode. Accept only the exact
        # canonical wire spelling, not integer/bytes/uppercase/braced aliases.
        if type(value) is str:
            parsed = UUID(value)
            if str(parsed) != value:
                raise ValueError("Canonical snapshot UUID required")
            return parsed
        if type(value) is not UUID:
            raise ValueError("Canonical snapshot UUID required")
        return value

    @model_validator(mode="after")
    def exact(self):
        StructuredSeedParameters.model_validate(_raw_fields(self.seed_parameters))
        StructuredObjective.model_validate(_raw_fields(self.objective))
        raw = _raw_fields(self.budget)
        if any(type(value) is not int for value in raw.values()):
            raise ValueError("Exact integer budget dimensions required")
        BudgetVector.model_validate(raw)
        if (self.objective.window.end - self.objective.window.start).days > 8:
            raise ValueError("Initial execution window is at most eight days")
        return self

    @property
    def sha256(self):
        return digest(type(self).model_validate(_raw_fields(self)))

    @property
    def required_process_units(self):
        # Cumulative units, NOT concurrent slot count or a full budget quote:
        # controller + seed evaluator + (SDK child + evaluator) per mutation.
        return 2 + 2 * self.maximum_mutation_slots

    def require_binding(self, binding):
        if type(binding) is not OEChatBinding:
            raise ValueError("Exact public OE operator binding required")
        binding = OEChatBinding.model_validate(_raw_fields(binding))
        if (self.model_profile_id, self.model_binding_sha256) != (
            binding.profile_id,
            binding.sha256,
        ):
            raise ValueError("Operator binding changed; preflight again")
        return binding


class ProviderStructuredSearchSubmit(ProviderStructuredSearchRequest):
    preflight_sha256: SHA

    def request(self):
        raw = _raw_fields(self)
        raw.pop("preflight_sha256")
        return ProviderStructuredSearchRequest.model_validate(raw)


class ProviderStructuredSubmissionReceipt(Closed):
    schema_version: Literal[
        "mineagent.oe-structured-provider-submission-receipt.v1"
    ] = "mineagent.oe-structured-provider-submission-receipt.v1"
    id: UUID
    run_id: UUID
    origin: Literal["authenticated_user_submission"] = "authenticated_user_submission"
    origin_user_id: UUID
    request_sha256: SHA
    preflight_sha256: SHA
    normalized_request: ProviderStructuredSearchRequest
    required_process_units: int = Field(ge=4, le=6)

    @model_validator(mode="after")
    def exact(self):
        request = ProviderStructuredSearchRequest.model_validate(
            _raw_fields(self.normalized_request)
        )
        if (self.request_sha256, self.required_process_units) != (
            request.sha256,
            request.required_process_units,
        ):
            raise ValueError(
                "Exact authenticated request and physical-unit count required"
            )
        return self


class ProviderPreflightReason(Closed):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=320)


class ProviderStructuredPreflight(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-preflight.v1"] = (
        "mineagent.oe-structured-provider-preflight.v1"
    )
    normalized_request: ProviderStructuredSearchRequest
    request_sha256: SHA
    preflight_sha256: SHA
    fixed_input_sha256: SHA | None
    runtime_profile_sha256: SHA | None
    model_binding: OEChatBinding | None
    required_budget: BudgetVector | None
    required_process_units: int = Field(ge=4, le=6)
    data_eligible: bool
    can_submit: bool
    reasons: tuple[ProviderPreflightReason, ...]
    qualification: Literal["fixed_snapshot_comparison_not_causal_gain"] = (
        "fixed_snapshot_comparison_not_causal_gain"
    )
    data_qualification: (
        Literal["synthetic_by_construction", "user_declaration_unverified"] | None
    )

    @model_validator(mode="after")
    def exact(self):
        request = ProviderStructuredSearchRequest.model_validate(
            _raw_fields(self.normalized_request)
        )
        if (self.request_sha256, self.required_process_units) != (
            request.sha256,
            request.required_process_units,
        ):
            raise ValueError("Exact request/physical-unit commitment required")
        if self.data_eligible != (self.data_qualification is not None):
            raise ValueError(
                "Data qualification requires actual eligible reconstruction"
            )
        if self.can_submit and (
            self.reasons
            or not self.data_eligible
            or self.fixed_input_sha256 is None
            or self.runtime_profile_sha256 is None
            or self.model_binding is None
            or self.required_budget is None
            or not request.budget.covers(self.required_budget)
        ):
            raise ValueError("Incomplete preflight cannot submit")
        if self.model_binding is not None:
            request.require_binding(self.model_binding)
        return self
