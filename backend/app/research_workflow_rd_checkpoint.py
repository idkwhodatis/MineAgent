"""Persist an actual stopped RD child boundary, independently of result release."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.execution_snapshot_history import historical_session
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import utcnow
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_terminal import read_rd_terminal_in_session
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)


def fail():
    raise DomainError(
        "workflow_rd_checkpoint_changed", "Actual RD stage boundary differs", 409
    )


def verify_rd_checkpoint(session, owner_id, workflow_id, checkpoint_id, settings):
    """Historical exact bytes and immutable graph; no current-head requirement."""
    require_schema(session)
    require_native_storage_guard(settings.storage_root)
    row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=checkpoint_id
        )
    )
    if row is None or (row.owner_id, row.workflow_id, row.id) != (
        owner_id,
        workflow_id,
        checkpoint_id,
    ):
        fail()
    try:
        saved = RDStageCheckpoint.model_validate_json(row.terminal_canonical)
    except (TypeError, ValueError):
        fail()
    if (
        canonical_bytes(saved).decode() != row.terminal_canonical
        or digest(saved) != row.terminal_sha256
        or (saved.owner_id, saved.workflow_id, saved.stage_id)
        != (owner_id, workflow_id, row.stage_id)
    ):
        fail()
    actual = read_rd_terminal_in_session(
        session, owner_id, workflow_id, row.stage_id, settings
    )
    if actual != saved:
        fail()
    return row, actual


def observe_rd_terminal(
    session, owner_id, workflow_id, stage_id, expected_revision, settings
):
    """Flush-only root CAS/checkpoint; actual guard and caller commit required."""
    require_schema(session)
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
    if (
        root is None
        or stage is None
        or (
            root.owner_id,
            root.id,
            stage.owner_id,
            stage.workflow_id,
            stage.id,
            stage.kind,
        )
        != (
            owner_id,
            workflow_id,
            owner_id,
            workflow_id,
            stage_id,
            "rd_factor_generation",
        )
    ):
        fail()
    prior = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, stage_id=stage_id
        )
    )
    if prior is not None:
        verified = verify_rd_checkpoint(
            session, owner_id, workflow_id, prior.id, settings
        )
        from backend.app.research_workflow_control import (
            acknowledge_verified_checkpoint_control,
        )

        if acknowledge_verified_checkpoint_control(
            root, stage_id, prior.id, expected_revision
        ):
            session.flush()
        return verified
    if (
        type(expected_revision) is not int
        or root.revision != expected_revision
        or root.revision >= 1000000
        or root.current_stage_id != stage.id
        or root.status
        not in {"running", "ready", "blocked", "pause_requested", "cancel_requested"}
    ):
        fail()
    try:
        plan = read_guidance_plan(session, root.plan_canonical)
        definition = RDStageDefinition.model_validate_json(stage.definition_canonical)
    except (TypeError, ValueError):
        fail()
    schedule = stage_schedule(plan)
    if (
        plan.owner_id != owner_id
        or canonical_bytes(plan).decode() != root.plan_canonical
        or plan.sha256 != root.plan_sha256
        or root.mode != plan.mode
        or canonical_bytes(definition).decode() != stage.definition_canonical
        or definition.sha256 != stage.definition_sha256
        or definition.plan_sha256 != plan.sha256
        or not 1 <= stage.ordinal <= len(schedule)
        or schedule[stage.ordinal - 1]
        != ("rd_factor_generation", definition.research_iteration)
    ):
        fail()
    actual = read_rd_terminal_in_session(
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
    if root.status == "cancel_requested":
        root.status, root.blocked_reason = "cancelled", None
    elif definition.research_iteration + 1 == plan.maximum_iterations:
        from packages.research_workflow.structured_plan import RDStructuredGuidancePlan

        if type(plan) is not RDStructuredGuidancePlan:
            root.status, root.blocked_reason = "completed", None
        elif root.status == "pause_requested" or root.mode == "pause_after_stage":
            root.status, root.blocked_reason = "paused", None
        else:
            # A successful RD checkpoint is not OE release or child admission.
            root.status, root.blocked_reason = "blocked", "workflow_structured_pending"
    elif root.status == "pause_requested" or root.mode == "pause_after_stage":
        root.status, root.blocked_reason = "paused", None
    else:
        # This checkpoint does not disclose results or auto-approve feedback.
        root.status, root.blocked_reason = "blocked", "workflow_feedback_pending"
    session.add(row)
    session.flush()
    return row, actual


def checkpoint_rd_stage(
    factory, settings, *, owner_id, workflow_id, stage_id, expected_revision
):
    with historical_session(factory, owner_id, settings) as session:
        row, actual = observe_rd_terminal(
            session, owner_id, workflow_id, stage_id, expected_revision, settings
        )
        result = (row.id, actual)
    return result  # historical_session successfully committed before ACK
