"""Actual successful guided predecessor qualification, not a v3 whitelist."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)


@dataclass(frozen=True)
class WorkflowSourceReference:
    """Lookup coordinates only. Every use performs full graph/byte verification."""

    workflow_id: UUID
    checkpoint_id: UUID

    def __post_init__(self):
        if type(self.workflow_id) is not UUID or type(self.checkpoint_id) is not UUID:
            raise ValueError("Exact workflow source UUIDs required")


def require_workflow_source(
    session, owner_id, source_run_id, snapshot_id, reference, settings
):
    if type(reference) is not WorkflowSourceReference:
        raise ValueError("Exact internal workflow source coordinates required")
    require_structured_workflow_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).filter_by(owner_id=owner_id, id=reference.workflow_id)
    )
    if root is None:
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    if (
        type(plan) is not RDStructuredGuidancePlan
        or plan.owner_id != owner_id
        or plan.sha256 != root.plan_sha256
    ):
        fail()
    row, actual = verify_rd_checkpoint(
        session, owner_id, root.id, reference.checkpoint_id, settings
    )
    stage = session.scalar(
        select(ResearchWorkflowStage).filter_by(
            owner_id=owner_id, workflow_id=root.id, id=actual.stage_id
        )
    )
    if stage is None or stage.kind != "rd_factor_generation":
        fail()
    definition = RDStageDefinition.model_validate_json(stage.definition_canonical)
    schedule = guidance_stage_schedule(plan)
    if (
        stage.ordinal != len(schedule) - 1
        or definition.research_iteration + 1 != plan.maximum_iterations
        or definition.plan_sha256 != plan.sha256
        or definition.sha256 != stage.definition_sha256
        or canonical_bytes(definition).decode() != stage.definition_canonical
        or (actual.child_run_id, actual.snapshot_id) != (source_run_id, snapshot_id)
        or stage.child_run_id != source_run_id
        or row.stage_id != stage.id
    ):
        fail()
    return plan, row, actual


def fail():
    raise DomainError(
        "workflow_structured_source_changed",
        "Actual successful workflow predecessor is unavailable",
        409,
    )
