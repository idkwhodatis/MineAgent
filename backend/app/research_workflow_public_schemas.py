"""Public finite workflow choices; no owner, container image, or operator secrets."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSeedRequest,
    RDProviderSubmissionRequest,
)
from packages.factors.contracts import Contract, Sha256, canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowMode

ProviderRequest = Annotated[
    RDProviderSubmissionRequest | RDProviderSeedRequest,
    Field(discriminator="schema_version"),
]


class WorkflowRequest(Contract):
    schema_version: Literal["mineagent.workflow-public-request.v1"] = (
        "mineagent.workflow-public-request.v1"
    )
    research: ProviderRequest
    mode: WorkflowMode = "continuous"
    maximum_iterations: int = Field(strict=True, ge=1, le=8)
    budget_ceiling: BudgetVector
    refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)
    external_definition: ExternalNormalizationDefinition | None = None
    allow_workflow_development_feedback_release: bool = Field(
        strict=True, default=False
    )

    @model_validator(mode="after")
    def exact(self):
        if (
            self.research.selected_steer_version_ids
            or self.research.max_iterations != 1
        ):
            raise ValueError(
                "Guidance belongs only to ordered workflow refs; each child is one generation"
            )
        if len({(v.origin, v.record_id, v.version_id) for v in self.refs}) != len(
            self.refs
        ):
            raise ValueError("Duplicate selected input version")
        if len(canonical_bytes(self)) > 131072:
            raise ValueError("Workflow public request exceeds byte bound")
        return self

    @property
    def sha256(self):
        return digest(self)


class WorkflowPreflight(Contract):
    schema_version: Literal["mineagent.workflow-public-preflight.v1"] = (
        "mineagent.workflow-public-preflight.v1"
    )
    request_sha256: Sha256
    preflight_sha256: Sha256
    plan_sha256: Sha256
    provider_preflight_sha256: Sha256
    resolved_inputs_sha256: Sha256
    runtime_profile_sha256: Sha256
    can_create: Literal[True] = True
    qualification: Literal["fresh_declaration_not_stage_execution_or_release"] = (
        "fresh_declaration_not_stage_execution_or_release"
    )


class WorkflowSubmit(Contract):
    schema_version: Literal["mineagent.workflow-public-submit.v1"] = (
        "mineagent.workflow-public-submit.v1"
    )
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    request: WorkflowRequest
    preflight_sha256: Sha256
