"""Flush-only owner/CAS control requests; never an execution or safe-stop grant.

A pause requested during a stage waits for its real terminal checkpoint. It does
not mutate that stage's selected inputs, cancel its deadline, or mark it stopped.
The successor dispatcher must acknowledge stop/settlement before recording a
checkpoint. Until that dispatcher and explicit schema capability are installed,
these unregistered services are unavailable.
"""

from uuid import uuid4

from sqlalchemy import select, text

from backend.app.models import utcnow
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCommand,
    ResearchWorkflowStage,
)
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowCommand, WorkflowCommandReceipt

SCHEMA = "mineagent.research-workflow-closure.v1"


def require_schema(session):
    versions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    try:
        valid = len(versions) == 1 and getattr(
            capabilities_for(versions[0]), "workflow_schema", None
        ) in {SCHEMA, "mineagent.research-workflow-closure.v2"}
    except ValueError:
        valid = False
    if not valid:
        raise DomainError(
            "workflow_unavailable", "Stage workflow schema is unavailable", 409
        )


def _fail(code, message):
    raise DomainError(code, message, 409)


def _replay(row, owner_id, command):
    raw = canonical_bytes(command).decode()
    if (row.owner_id, row.workflow_id, row.command_canonical, row.command_sha256) != (
        owner_id,
        command.workflow_id,
        raw,
        digest(command),
    ):
        _fail(
            "workflow_command_conflict",
            "Command key already has different immutable input",
        )
    try:
        result = WorkflowCommandReceipt.model_validate_json(row.result_canonical)
        if canonical_bytes(result).decode() != row.result_canonical or (
            result.workflow_id,
            result.command_id,
            result.command_sha256,
        ) != (row.workflow_id, row.id, row.command_sha256):
            raise ValueError("Command receipt differs")
        return result
    except (ValueError, TypeError):
        _fail(
            "workflow_command_inconsistent", "Retained command receipt is inconsistent"
        )


def request_control(session, owner_id, command, *, settings=None, admission=None):
    """Record a request atomically; exact retry precedes the revision comparison.

    Resume after an actual stage requires the fixed quota-owned checkpoint
    adapter. An initial paused workflow needs no invented process evidence.
    """
    command = WorkflowCommand.model_validate(command)
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .where(
            ResearchWorkflow.owner_id == owner_id,
            ResearchWorkflow.id == command.workflow_id,
        )
        .with_for_update(nowait=True)
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    prior = session.scalar(
        select(ResearchWorkflowCommand).where(
            ResearchWorkflowCommand.owner_id == owner_id,
            ResearchWorkflowCommand.workflow_id == root.id,
            ResearchWorkflowCommand.idempotency_key == command.idempotency_key,
        )
    )
    if prior is not None:
        return _replay(prior, owner_id, command)
    if root.revision != command.expected_revision:
        _fail(
            "workflow_revision_conflict",
            "Workflow changed; reload before sending this command",
        )
    if root.revision >= 1_000_000:
        _fail("workflow_revision_limit", "Workflow revision limit reached")
    has_stages = (
        session.scalar(
            select(ResearchWorkflowStage.id)
            .where(
                ResearchWorkflowStage.owner_id == owner_id,
                ResearchWorkflowStage.workflow_id == root.id,
            )
            .limit(1)
        )
        is not None
    )
    if not has_stages and (
        root.current_stage_id is not None or root.latest_checkpoint_id is not None
    ):
        _fail(
            "workflow_graph_inconsistent",
            "Workflow stage pointers differ from retained graph",
        )
    if has_stages and root.current_stage_id is None:
        _fail(
            "workflow_graph_inconsistent",
            "A retained stage requires its exact current pointer",
        )
    if not has_stages and root.status in {
        "running",
        "pause_requested",
        "cancel_requested",
    }:
        _fail("workflow_graph_inconsistent", "Active workflow has no actual stage")
    if root.status in {"completed", "cancelled"}:
        _fail(
            "workflow_terminal", "Completed or cancelled workflows cannot be rewritten"
        )
    next_reason = None
    if command.action == "pause_after_stage":
        if root.status == "cancel_requested":
            _fail("workflow_cancellation_pending", "Cancellation cannot become a pause")
        # No positive-stop claim for an already admitted stage.
        next_status = "paused" if not has_stages else "pause_requested"
    elif command.action == "cancel":
        next_status = "cancelled" if not has_stages else "cancel_requested"
    else:
        if root.status != "paused":
            _fail("workflow_not_paused", "Resume requires an acknowledged safe pause")
        if has_stages:
            if settings is None or admission is None:
                _fail(
                    "workflow_checkpoint_adapter_unavailable",
                    "Actual checkpoint guard required",
                )
            from backend.app.research_workflow_resume import verify_resume_checkpoint

            next_reason = verify_resume_checkpoint(
                session, owner_id, root, settings, admission=admission
            )
            if next_reason == "workflow_finite_plan_complete":
                next_status, next_reason = "completed", None
            else:
                next_status = "blocked"
        else:
            next_status = "ready"
    now = utcnow()
    root.revision += 1
    root.status = next_status
    root.blocked_reason = next_reason
    root.updated_at = now
    result = WorkflowCommandReceipt(
        workflow_id=root.id,
        command_id=uuid4(),
        command_sha256=digest(command),
        revision=root.revision,
        status=root.status,
        checkpoint_id=root.latest_checkpoint_id,
        current_stage_id=root.current_stage_id,
    )
    row = ResearchWorkflowCommand(
        id=result.command_id,
        owner_id=owner_id,
        workflow_id=root.id,
        idempotency_key=command.idempotency_key,
        command_canonical=canonical_bytes(command).decode(),
        command_sha256=digest(command),
        result_canonical=canonical_bytes(result).decode(),
        created_at=now,
    )
    session.add(row)
    session.flush()
    return result


def acknowledge_verified_checkpoint_control(
    root, stage_id, checkpoint_id, expected_revision
):
    """Only called after an actual checkpoint's physical verifier succeeds.

    A later pause/cancel request may target a stage whose immutable checkpoint
    already exists. Re-reading that checkpoint must acknowledge the new control
    without rewriting the checkpoint or manufacturing a second terminal.
    """
    if root.current_stage_id != stage_id or root.status not in {
        "pause_requested",
        "cancel_requested",
    }:
        return False
    if (
        root.latest_checkpoint_id != checkpoint_id
        or root.revision != expected_revision
        or root.revision >= 1000000
    ):
        _fail("workflow_revision_conflict", "Checkpoint control changed")
    root.status = "cancelled" if root.status == "cancel_requested" else "paused"
    root.blocked_reason = None
    root.revision += 1
    root.updated_at = utcnow()
    return True
