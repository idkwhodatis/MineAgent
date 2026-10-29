"""Explicit opt-in to one terminal OE stage; not snapshot release consent."""

from typing import Literal

from pydantic import Field

from backend.app.oe_structured_submission_schemas import StructuredSeedParameters
from backend.app.research_workflow_public_schemas import (
    WorkflowPreflight,
    WorkflowRequest,
    WorkflowSubmit,
)
from packages.factors.contracts import Contract, Sha256
from packages.openevolve_structured.objective import StructuredObjective
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.admission import CreateWorkflow
from packages.research_workflow.structured_plan import RDStructuredGuidancePlan


class WorkflowStructuredChoice(Contract):
    seed_parameters: StructuredSeedParameters
    objective: StructuredObjective
    budget: BudgetVector
    maximum_mutation_slots: int = Field(strict=True, ge=1, le=2)
    model_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_binding_sha256: Sha256

    @property
    def required_process_units(self):
        return 2 + 2 * self.maximum_mutation_slots


class StructuredWorkflowRequest(WorkflowRequest):
    schema_version: Literal["mineagent.workflow-public-request.v2"] = (
        "mineagent.workflow-public-request.v2"
    )
    structured: WorkflowStructuredChoice


class StructuredWorkflowSubmit(WorkflowSubmit):
    schema_version: Literal["mineagent.workflow-public-submit.v2"] = (
        "mineagent.workflow-public-submit.v2"
    )
    request: StructuredWorkflowRequest


class StructuredWorkflowPreflight(WorkflowPreflight):
    schema_version: Literal["mineagent.workflow-public-preflight.v2"] = (
        "mineagent.workflow-public-preflight.v2"
    )
    structured_template_sha256: Sha256


class CreateStructuredWorkflow(CreateWorkflow):
    schema_version: Literal["mineagent.workflow-create.v2"] = (
        "mineagent.workflow-create.v2"
    )
    plan: RDStructuredGuidancePlan
