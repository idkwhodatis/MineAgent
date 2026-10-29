"""Owner requests for finite plans and future-only immutable selections."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.factors.contracts import Contract
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_workflow.plan import RDGuidancePlan


class CreateWorkflow(Contract):
    schema_version: Literal["mineagent.workflow-create.v1"] = (
        "mineagent.workflow-create.v1"
    )
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    plan: RDGuidancePlan
    refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)
    external_definition: ExternalNormalizationDefinition | None = None
    # Host-retained exact public submit body for original replay, not a grant.
    public_submission_canonical: str | None = Field(default=None, max_length=262144)


class SelectWorkflowInputs(Contract):
    schema_version: Literal["mineagent.workflow-select-inputs.v1"] = (
        "mineagent.workflow-select-inputs.v1"
    )
    workflow_id: UUID
    expected_revision: int = Field(strict=True, ge=1, le=1_000_000)
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    refs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)


class WorkflowCreated(Contract):
    schema_version: Literal["mineagent.workflow-created.v1"] = (
        "mineagent.workflow-created.v1"
    )
    workflow_id: UUID
    input_revision_id: UUID
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: Literal[1] = 1
    status: Literal["ready", "blocked"]
    current_stage_id: UUID | None
