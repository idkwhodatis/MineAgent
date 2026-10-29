"""Explicit finite RD-to-OE declaration; no inferred release or launch authority.

The legacy RD-only plan and its serializer are intentionally unchanged. The
terminal structured child must later resolve its actual successful predecessor,
release and fresh operator admission; this template cannot manufacture them.
"""

from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, canonical_bytes
from packages.openevolve_structured.objective import StructuredObjective
from packages.openevolve_structured.provider_contracts import OEChatBinding
from packages.openevolve_structured.provider_run import StructuredRuntimePins
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.plan import RDGuidancePlan, stage_schedule


class WorkflowStructuredSeed(Contract):
    threshold: float = Field(strict=True, ge=-1e15, le=1e15)
    top_k: int = Field(strict=True, ge=1, le=100)
    maximum_weight: float = Field(strict=True, gt=0, le=1)
    maximum_gross_weight: float = Field(strict=True, gt=0, le=1)


class WorkflowStructuredTemplate(Contract):
    schema_version: Literal["mineagent.workflow-structured-template.v1"] = (
        "mineagent.workflow-structured-template.v1"
    )
    seed_parameters: WorkflowStructuredSeed
    objective: StructuredObjective
    model_binding: OEChatBinding
    runtime: StructuredRuntimePins
    budget: BudgetVector
    maximum_mutation_slots: int = Field(strict=True, ge=1, le=2)
    source_policy: Literal["actual_final_successful_rd_checkpoint"] = (
        "actual_final_successful_rd_checkpoint"
    )
    release_policy: Literal["explicit_snapshot_release_required"] = (
        "explicit_snapshot_release_required"
    )

    @model_validator(mode="after")
    def finite(self):
        if self.model_binding.pricing.status != "operator_declared":
            raise ValueError("Unknown OE pricing cannot fund a continuous workflow")
        if (self.objective.window.end - self.objective.window.start).days > 8:
            raise ValueError("Existing bounded structured window is at most eight days")
        if self.budget.process_slots < 2 + 2 * self.maximum_mutation_slots:
            raise ValueError(
                "Structured template lacks original cumulative process units"
            )
        return self


class RDStructuredGuidancePlan(RDGuidancePlan):
    schema_version: Literal["mineagent.rd-structured-guidance-workflow-plan.v1"] = (
        "mineagent.rd-structured-guidance-workflow-plan.v1"
    )
    profile: Literal["rd_guidance_then_structured_oe.v1"] = (
        "rd_guidance_then_structured_oe.v1"
    )
    structured_template: WorkflowStructuredTemplate

    @model_validator(mode="after")
    def combined_ceiling(self):
        minimum = BudgetVector(
            **{
                key: getattr(self.research_template.budget, key)
                * self.maximum_iterations
                for key in BudgetVector.model_fields
            }
        ).add(BudgetVector(storage_bytes=65536 * (self.maximum_iterations - 1)))
        minimum = minimum.add(self.structured_template.budget)
        if not self.budget_ceiling.covers(minimum):
            raise ValueError(
                "Workflow ceiling must also fund its explicit terminal OE child"
            )
        signal, execution = (
            self.research_template.input.window,
            self.structured_template.objective.window,
        )
        if signal.end - signal.start != timedelta(days=1):
            raise ValueError("Terminal structured OE requires one frozen signal day")
        if not execution.start <= signal.start < signal.end < execution.end:
            raise ValueError(
                "Structured window must contain frozen RD signal and later execution"
            )
        return self

    def rd_plan(self) -> RDGuidancePlan:
        raw = self.model_dump(mode="json")
        raw.pop("structured_template")
        raw["schema_version"] = "mineagent.rd-guidance-workflow-plan.v1"
        raw["profile"] = "rd_guidance_finite_loop.v1"
        return RDGuidancePlan.model_validate_json(canonical_bytes(raw))


def parse_guidance_plan(raw) -> RDGuidancePlan | RDStructuredGuidancePlan:
    """Exact explicit version dispatch, never upgrade a legacy retained plan."""
    if type(raw) is not bytes or len(raw) > 1048576:
        raise ValueError("Retained canonical plan bytes required")
    import json

    # Contract validation below enforces the closed grammar; version selects
    # only a parser, never grants a schema capability or workflow transition.
    value = json.loads(raw)
    if canonical_bytes(value) != raw:
        raise ValueError("Canonical unique finite workflow plan required")
    version = value.get("schema_version") if type(value) is dict else None
    if version == "mineagent.rd-guidance-workflow-plan.v1":
        return RDGuidancePlan.model_validate_json(raw)
    if version == "mineagent.rd-structured-guidance-workflow-plan.v1":
        return RDStructuredGuidancePlan.model_validate_json(raw)
    raise ValueError("Unknown finite workflow plan version")


def guidance_stage_schedule(plan):
    checked = parse_guidance_plan(canonical_bytes(plan))
    if type(checked) is RDGuidancePlan:
        return stage_schedule(checked)
    return (*stage_schedule(checked.rd_plan()), ("structured_factor_execution", None))


def terminal_structured_budget(plan):
    checked = parse_guidance_plan(canonical_bytes(plan))
    return (
        checked.structured_template.budget
        if type(checked) is RDStructuredGuidancePlan
        else BudgetVector()
    )
