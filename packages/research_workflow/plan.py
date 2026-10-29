"""One finite RD-guidance profile, not a general executable workflow language.

The template is a retained declaration, not permission to call an operator. Every
NEW child performs normal provider/source/owner/budget admission again. The host
must resolve actual selected versions, safe predecessor checkpoint and eligible
feedback before constructing its distinct workflow-bound child configuration.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes, digest
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.rdagent_adapter.contracts import ImageDigest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import StageKind, WorkflowMode
from packages.research_workflow.provider_config import WorkflowProviderResearchTemplate


class RDGuidancePlan(Contract):
    schema_version: Literal["mineagent.rd-guidance-workflow-plan.v1"] = (
        "mineagent.rd-guidance-workflow-plan.v1"
    )
    owner_id: UUID
    profile: Literal["rd_guidance_finite_loop.v1"] = "rd_guidance_finite_loop.v1"
    mode: WorkflowMode = "continuous"
    maximum_iterations: int = Field(strict=True, ge=1, le=8)
    normalize_external_first: bool = Field(strict=True, default=False)
    research_template: WorkflowProviderResearchTemplate
    objective: DirectObjectiveDeclaration
    source_policy: ConfiguredSourcePolicy
    qlib_installed_parent_image_id: ImageDigest
    analysis_dependency_lock_sha256: Sha256
    initial_parent_factor_version_id: UUID | None
    budget_ceiling: BudgetVector
    failure_policy: Literal["block_until_explicit_new_stage"] = (
        "block_until_explicit_new_stage"
    )
    # Initial user choice, immutable with this plan; not a global or retrospective grant.
    allow_workflow_development_feedback_release: bool = Field(
        strict=True, default=False
    )
    feedback_release_scope: Literal["own_bound_workflow_development_outputs_only"] = (
        "own_bound_workflow_development_outputs_only"
    )
    feedback_policy: Literal["released_development_direct_analysis_only"] = (
        "released_development_direct_analysis_only"
    )
    lineage_policy: Literal["next_child_uses_actual_previous_factor_version"] = (
        "next_child_uses_actual_previous_factor_version"
    )
    physical_policy: Literal["new_child_new_unit_original_deadline"] = (
        "new_child_new_unit_original_deadline"
    )

    @model_validator(mode="after")
    def finite_exact_template(self):
        template = self.research_template
        if (
            template.steer_snapshot.owner_id != self.owner_id
            or template.steer_snapshot.versions
        ):
            raise ValueError(
                "All guidance belongs to the explicit combined input snapshot, not a hidden template steer"
            )
        if template.max_iterations != 1:
            raise ValueError(
                "Each child performs one generation; workflow iterations are separately bounded"
            )
        if self.objective.window != template.input.window:
            raise ValueError("Child factor analysis and generation window differ")
        if template.model_binding.pricing.status != "operator_declared":
            raise ValueError(
                "Unknown pricing cannot fund a continuous cost-capped workflow"
            )
        minimum = BudgetVector(
            **{
                key: getattr(template.budget, key) * self.maximum_iterations
                for key in BudgetVector.model_fields
            }
        )
        # Incremental deterministic feedback peak: one <=32KiB canonical
        # proposal plus one <=32KiB atomic temporary; prior outputs are retained
        # and already counted, never charged again as a new engine/model unit.
        minimum = minimum.add(
            BudgetVector(storage_bytes=65536 * (self.maximum_iterations - 1))
        )
        if not self.budget_ceiling.covers(minimum):
            raise ValueError(
                "Workflow ceiling cannot fund its declared child budget bounds"
            )
        if len(canonical_bytes(self)) > 1048576:
            raise ValueError("Finite workflow plan exceeds retained byte bound")
        return self

    @property
    def sha256(self):
        return digest(self)


def stage_schedule(plan: RDGuidancePlan) -> tuple[tuple[StageKind, int | None], ...]:
    """Pure order only. Every edge still needs fresh terminal/release admission.

    Iteration numbers are zero-based; normalization is not a research iteration.
    No OE stage, model evaluator, arbitrary Python, or implicit structured engine
    is smuggled into this first profile. A separate installed profile may later
    append a structured stage with its real persisted execution declaration.
    """
    plan = RDGuidancePlan.model_validate_json(canonical_bytes(plan))
    result: list[tuple[StageKind, int | None]] = []
    if plan.normalize_external_first:
        result.append(("external_normalization", None))
    for ordinal in range(plan.maximum_iterations):
        result.append(("rd_factor_generation", ordinal))
        if ordinal + 1 < plan.maximum_iterations:
            result.append(("experiment_feedback_projection", ordinal))
    return tuple(result)
