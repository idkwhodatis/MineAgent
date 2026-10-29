"""Fresh finite RD child acceptance, never a historical worker/resume grant."""

from dataclasses import dataclass

from sqlalchemy import select

from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_local_checkpoint import observe_external_terminal
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.plan import RDGuidancePlan
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)


@dataclass(frozen=True)
class CurrentRDStage:
    root: ResearchWorkflow
    stage: ResearchWorkflowStage
    plan: RDGuidancePlan
    selection: WorkflowInputSnapshot
    definition: RDStageDefinition


def fail():
    raise DomainError("workflow_rd_stage_changed", "RD stage admission differs", 409)


def require_current_rd_stage(
    session, owner_id, workflow_id, stage_id, settings, *, admission
):
    """Check actual locked graph inside the caller's quota-owned transaction.

    Later iterations require the actual prior child and released feedback
    boundary. Immutable worker validation never calls this current-head helper.
    """
    require_schema(session)
    require_publication_admission(session, settings, admission)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .where(
            ResearchWorkflow.owner_id == owner_id, ResearchWorkflow.id == workflow_id
        )
        .with_for_update(nowait=True)
    )
    stages = tuple(
        session.scalars(
            select(ResearchWorkflowStage)
            .where(
                ResearchWorkflowStage.owner_id == owner_id,
                ResearchWorkflowStage.workflow_id == workflow_id,
            )
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(18)
        )
    )
    if root is None or not stages or len(stages) > 17:
        fail()
    stage = stages[-1]
    if (
        (root.owner_id, root.id, root.current_stage_id)
        != (owner_id, workflow_id, stage_id)
        or root.status != "running"
        or stage.id != stage_id
        or stage.kind != "rd_factor_generation"
        or stage.child_run_id is None
        or stage.input_revision_id != root.current_input_revision_id
        or len(root.plan_canonical.encode()) > 1048576
        or len(stage.definition_canonical.encode()) > 8192
    ):
        fail()
    revision = session.scalar(
        select(ResearchWorkflowRevision).where(
            ResearchWorkflowRevision.owner_id == owner_id,
            ResearchWorkflowRevision.workflow_id == workflow_id,
            ResearchWorkflowRevision.id == stage.input_revision_id,
        )
    )
    if revision is None or len(revision.inputs_canonical.encode()) > 32768:
        fail()
    try:
        plan = read_guidance_plan(session, root.plan_canonical)
        selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
        definition = RDStageDefinition.model_validate_json(stage.definition_canonical)
    except (TypeError, ValueError):
        fail()
    if (
        plan.owner_id != owner_id
        or root.mode != plan.mode
        or canonical_bytes(plan).decode() != root.plan_canonical
        or plan.sha256 != root.plan_sha256
        or root.budget_canonical != canonical_bytes(plan.budget_ceiling).decode()
        or root.budget_sha256 != digest(plan.budget_ceiling)
        or (revision.owner_id, revision.workflow_id, revision.id)
        != (owner_id, workflow_id, stage.input_revision_id)
        or (selection.owner_id, selection.workflow_id, selection.revision_id)
        != (owner_id, workflow_id, stage.input_revision_id)
        or canonical_bytes(selection).decode() != revision.inputs_canonical
        or selection.sha256 != revision.inputs_sha256
        or canonical_bytes(definition).decode() != stage.definition_canonical
        or definition.sha256 != stage.definition_sha256
        or definition.plan_sha256 != plan.sha256
        or not 0 <= definition.research_iteration < plan.maximum_iterations
        or stage.allocation_canonical
        != canonical_bytes(plan.research_template.budget).decode()
        or stage.allocation_sha256 != digest(plan.research_template.budget)
    ):
        fail()
    expected_count = (
        2 if plan.normalize_external_first else 1
    ) + 2 * definition.research_iteration
    if len(stages) != expected_count:
        fail()
    schedule = stage_schedule(plan)
    for ordinal, row in enumerate(stages, 1):
        if (row.owner_id, row.workflow_id, row.ordinal, row.kind) != (
            owner_id,
            workflow_id,
            ordinal,
            schedule[ordinal - 1][0],
        ):
            fail()
    if definition.research_iteration > 0:
        from backend.app.research_workflow_native_guard import (
            native_guard_from_publication,
        )
        from backend.app.research_workflow_rd_predecessor import verify_rd_predecessor

        with native_guard_from_publication(session, settings, admission=admission):
            predecessor = verify_rd_predecessor(
                session, owner_id, workflow_id, stage_id, settings
            )
        if (
            predecessor.definition != definition
            or predecessor.feedback_checkpoint_id != root.latest_checkpoint_id
        ):
            fail()
    elif (
        definition.parent_source_stage_id is not None
        or definition.parent_factor_version_id != plan.initial_parent_factor_version_id
    ):
        fail()
    elif plan.normalize_external_first:
        previous = stages[0]
        checkpoint = session.scalar(
            select(ResearchWorkflowCheckpoint).where(
                ResearchWorkflowCheckpoint.owner_id == owner_id,
                ResearchWorkflowCheckpoint.workflow_id == workflow_id,
                ResearchWorkflowCheckpoint.stage_id == previous.id,
            )
        )
        if (
            checkpoint is None
            or checkpoint.id != definition.predecessor_checkpoint_id
            or root.latest_checkpoint_id != checkpoint.id
        ):
            fail()
        checkpoint_id, facts = observe_external_terminal(
            session,
            owner_id,
            workflow_id,
            previous.id,
            root.revision,
            settings,
            admission=admission,
        )
        if checkpoint_id != checkpoint.id or facts.state != "succeeded":
            fail()
    elif (
        definition.predecessor_checkpoint_id is not None
        or root.latest_checkpoint_id is not None
    ):
        fail()
    require_publication_admission(session, settings, admission)
    return CurrentRDStage(root, stage, plan, selection, definition)
