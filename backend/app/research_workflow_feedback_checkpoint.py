"""Close a real feedback terminal under the existing publisher's commit boundary."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import utcnow
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_feedback import _binding
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowRevision,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.feedback_checkpoint import (
    FeedbackCheckpoint,
    FeedbackCheckpointObject,
)


def fail():
    raise DomainError(
        "workflow_feedback_checkpoint_changed",
        "Feedback terminal boundary differs",
        409,
    )


def observe_feedback_terminal(
    session,
    owner_id,
    workflow_id,
    stage_id,
    expected_revision,
    settings,
    *,
    admission,
    prepared_read,
):
    from backend.app.research_input_feedback import validate_feedback_terminal

    require_publication_admission(session, settings, admission)
    _try_neutral_lock(session, f"submit:{owner_id}")
    locked = session.scalar(
        select(ResearchWorkflow)
        .filter_by(owner_id=owner_id, id=workflow_id)
        .with_for_update(nowait=True)
    )
    root, stage, _, definition, rd_checkpoint, _ = _binding(
        session, owner_id, workflow_id, stage_id
    )
    if root is not locked:
        fail()
    prior = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, stage_id=stage_id
        )
    )
    if prior is None and (
        type(expected_revision) is not int
        or root.revision != expected_revision
        or root.revision >= 1000000
        or root.current_stage_id != stage_id
        or root.status
        not in {"ready", "running", "blocked", "pause_requested", "cancel_requested"}
    ):
        fail()
    revision = session.scalar(
        select(ResearchWorkflowRevision).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=stage.input_revision_id
        )
    )
    if revision is None:
        fail()
    selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
    if (
        canonical_bytes(selection).decode() != revision.inputs_canonical
        or selection.sha256 != revision.inputs_sha256
        or (selection.owner_id, selection.workflow_id, selection.revision_id)
        != (owner_id, workflow_id, stage.input_revision_id)
    ):
        fail()
    facts = validate_feedback_terminal(
        session,
        owner_id,
        workflow_id,
        stage_id,
        settings,
        admission=admission,
        prepared_read=prepared_read,
    )
    payload = FeedbackCheckpoint(
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        input_revision_id=stage.input_revision_id,
        input_sha256=selection.sha256,
        definition_sha256=stage.definition_sha256,
        allocation_sha256=stage.allocation_sha256,
        rd_checkpoint_id=rd_checkpoint.id,
        source=definition.source,
        derivation_id=facts.derivation_id,
        terminal_event_id=facts.terminal_event_id,
        terminal_event_sha256=facts.terminal_event_sha256,
        state=facts.state,
        outputs=facts.outputs,
        objects=tuple(
            FeedbackCheckpointObject(
                id=o.id, kind=o.kind, sha256=o.sha256, size_bytes=o.size_bytes
            )
            for o in facts.objects
        ),
        exposure_ids=facts.exposure_ids,
    )
    if prior is not None:
        if (
            prior.owner_id,
            prior.workflow_id,
            prior.stage_id,
            prior.terminal_canonical,
            prior.terminal_sha256,
        ) != (
            owner_id,
            workflow_id,
            stage_id,
            canonical_bytes(payload).decode(),
            digest(payload),
        ):
            fail()
        from backend.app.research_workflow_control import (
            acknowledge_verified_checkpoint_control,
        )

        if acknowledge_verified_checkpoint_control(
            root, stage_id, prior.id, expected_revision
        ):
            session.flush()
        return prior.id, payload
    row = ResearchWorkflowCheckpoint(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        terminal_canonical=canonical_bytes(payload).decode(),
        terminal_sha256=digest(payload),
    )
    root.latest_checkpoint_id = row.id
    root.revision += 1
    root.updated_at = utcnow()
    if root.status == "cancel_requested":
        root.status, root.blocked_reason = "cancelled", None
    elif facts.state != "succeeded":
        root.status, root.blocked_reason = "blocked", "workflow_stage_failed"
    elif root.status == "pause_requested" or root.mode == "pause_after_stage":
        root.status, root.blocked_reason = "paused", None
    else:
        root.status, root.blocked_reason = "blocked", "workflow_next_child_pending"
    session.add(row)
    session.flush()
    return row.id, payload


def checkpoint_feedback_stage(
    factory, settings, *, owner_id, workflow_id, stage_id, expected_revision
):
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_services import publication
    from backend.app.research_input_history import read_derivation_history
    from backend.app.research_workflow_feedback import read_released_feedback

    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        # Read only lifecycle metadata before deciding whether a released result
        # is needed. Cancellation must not disclose a value or create exposure.
        with factory() as session:
            history = read_derivation_history(session, owner_id, workflow_id, stage_id)
            if history.terminal_event_id is None:
                fail()
        read = (
            read_released_feedback(
                factory,
                settings,
                owner_id=owner_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
            )
            if history.status == "succeeded"
            else None
        )
        with factory() as session:
            with publication(
                session, owner_id, settings, peak_bytes=0, peak_files=0
            ) as admission:
                result = observe_feedback_terminal(
                    session,
                    owner_id,
                    workflow_id,
                    stage_id,
                    expected_revision,
                    settings,
                    admission=admission,
                    prepared_read=read,
                )
            return result  # The quota guard commits exactly once before ACK.
