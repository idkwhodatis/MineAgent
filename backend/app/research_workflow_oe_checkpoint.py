"""Persist only a fully stopped and settled final structured OE stage."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.execution_snapshot_history import historical_session
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import utcnow
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import (
    acknowledge_verified_checkpoint_control,
)
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_oe_terminal import read_oe_terminal_in_session
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.oe_checkpoint import OEStageCheckpoint
from packages.research_workflow.oe_stage import OEStageDefinition
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)


def fail():
    raise DomainError(
        "workflow_oe_checkpoint_changed", "Actual OE stage boundary differs", 409
    )


def verify_oe_checkpoint(session, owner_id, workflow_id, checkpoint_id, settings):
    require_structured_workflow_schema(session)
    require_native_storage_guard(settings.storage_root)
    row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=checkpoint_id
        )
    )
    if row is None:
        fail()
    saved = OEStageCheckpoint.model_validate_json(row.terminal_canonical)
    if (
        canonical_bytes(saved).decode() != row.terminal_canonical
        or digest(saved) != row.terminal_sha256
        or (saved.owner_id, saved.workflow_id, saved.stage_id)
        != (owner_id, workflow_id, row.stage_id)
    ):
        fail()
    actual = read_oe_terminal_in_session(
        session, owner_id, workflow_id, row.stage_id, settings
    )
    if actual != saved:
        fail()
    return row, actual


def observe_oe_terminal(
    session, owner_id, workflow_id, stage_id, expected_revision, settings
):
    require_structured_workflow_schema(session)
    require_native_storage_guard(settings.storage_root)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .filter_by(owner_id=owner_id, id=workflow_id)
        .with_for_update(nowait=True)
    )
    stage = session.scalar(
        select(ResearchWorkflowStage).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=stage_id
        )
    )
    if root is None or stage is None or stage.kind != "structured_factor_execution":
        fail()
    old = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, stage_id=stage_id
        )
    )
    if old is not None:
        result = verify_oe_checkpoint(session, owner_id, workflow_id, old.id, settings)
        if acknowledge_verified_checkpoint_control(
            root, stage_id, old.id, expected_revision
        ):
            session.flush()
        return result
    if (
        type(expected_revision) is not int
        or root.revision != expected_revision
        or root.revision >= 1000000
        or root.current_stage_id != stage_id
        or root.status
        not in {"running", "blocked", "pause_requested", "cancel_requested"}
    ):
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    definition = OEStageDefinition.model_validate_json(stage.definition_canonical)
    if (
        type(plan) is not RDStructuredGuidancePlan
        or plan.sha256 != root.plan_sha256
        or stage.ordinal != len(guidance_stage_schedule(plan))
        or definition.plan_sha256 != plan.sha256
        or definition.sha256 != stage.definition_sha256
        or canonical_bytes(definition).decode() != stage.definition_canonical
    ):
        fail()
    actual = read_oe_terminal_in_session(
        session, owner_id, workflow_id, stage_id, settings
    )
    row = ResearchWorkflowCheckpoint(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        terminal_canonical=canonical_bytes(actual).decode(),
        terminal_sha256=digest(actual),
    )
    root.latest_checkpoint_id = row.id
    root.revision += 1
    root.updated_at = utcnow()
    # Pause/cancel after admission waits for this actual closure. No next stage
    # exists in this finite plan and no completed experiment is rewritten.
    root.status = (
        "cancelled"
        if root.status == "cancel_requested"
        else "paused"
        if root.status == "pause_requested"
        else "completed"
    )
    root.blocked_reason = None
    session.add(row)
    session.flush()
    return row, actual


def checkpoint_oe_stage(
    factory, settings, *, owner_id, workflow_id, stage_id, expected_revision
):
    with historical_session(factory, owner_id, settings) as session:
        row, actual = observe_oe_terminal(
            session, owner_id, workflow_id, stage_id, expected_revision, settings
        )
        result = row.id, actual
    return result
