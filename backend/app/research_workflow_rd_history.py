"""Actual child receipt/status metadata; no inferred prompt or stop evidence."""

from sqlalchemy import select

from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelTransport,
)
from backend.app.research_workflow_models import (
    ResearchWorkflowCheckpoint,
    ResearchWorkflowModelConsumption,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.research_inputs.contracts import SourceOutputRef
from packages.research_workflow.contracts import StageConsumption, WorkflowStageHistory


def fail():
    raise DomainError(
        "workflow_child_history_changed", "Retained child graph differs", 409
    )


def read_rd_stage_history(session, owner_id, root, stage, settings):
    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from backend.app.rdagent_workflow_saved import verify_workflow_submission

    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(
            owner_id=owner_id, workflow_id=root.id, stage_id=stage.id
        )
    )
    if row is None:
        fail()
    row = verify_workflow_submission(session, owner_id, row.id, settings)
    run = session.scalar(
        select(ResearchRun).filter_by(owner_id=owner_id, id=row.run_id)
    )
    if run is None or (
        row.workflow_id,
        row.stage_id,
        row.run_id,
        run.owner_id,
        run.id,
    ) != (root.id, stage.id, stage.child_run_id, owner_id, stage.child_run_id):
        fail()
    try:
        config = WorkflowFactorResearchConfig.model_validate_json(
            canonical_bytes(run.config)
        )
    except (TypeError, ValueError):
        fail()
    binding = config.workflow_inputs.binding
    if (
        binding.owner_id,
        binding.workflow_id,
        binding.stage_id,
        binding.input_revision_id,
        binding.definition_sha256,
    ) != (
        owner_id,
        root.id,
        stage.id,
        stage.input_revision_id,
        stage.definition_sha256,
    ):
        fail()
    # Presence is not proof of prompt delivery/semantic compliance. The actual
    # request-byte observer will separately project its exact operation evidence.
    attempts = tuple(
        session.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.run_id == run.id)
            .limit(1025)
        )
    )
    if len(attempts) > 1024:
        fail()
    current = None
    if run.active_attempt_id is not None:
        matches = [
            a
            for a in attempts
            if a.id == run.active_attempt_id
            and a.run_id == run.id
            and a.generation == run.generation
        ]
        if len(matches) != 1:
            fail()
        current = matches[0]
    operation = session.scalar(
        select(ResearchModelOperation.id)
        .where(ResearchModelOperation.run_id == run.id)
        .limit(1)
    )
    evidence = session.scalar(
        select(ResearchWorkflowModelConsumption.id)
        .where(ResearchWorkflowModelConsumption.stage_id == stage.id)
        .limit(1)
    )
    checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint.id)
        .where(ResearchWorkflowCheckpoint.stage_id == stage.id)
        .limit(1)
    )
    terminal = None
    if checkpoint is not None:
        from backend.app.research_workflow_input_reads import retained_input_guard
        from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint

        with retained_input_guard(settings):
            _, terminal = verify_rd_checkpoint(
                session, owner_id, root.id, checkpoint, settings
            )
        if (
            terminal.stage_id,
            terminal.child_run_id,
            terminal.submission_id,
            terminal.input_revision_id,
            terminal.input_sha256,
            terminal.definition_sha256,
        ) != (
            stage.id,
            run.id,
            row.id,
            stage.input_revision_id,
            binding.input_sha256,
            stage.definition_sha256,
        ):
            fail()
    requests = ()
    if terminal is not None:
        # The terminal reader has already reconstructed exact first guidance
        # bytes and the full SDK response/stop/charge graph. Only that single
        # actual admitted inclusion row may become a submitted-history claim.
        inclusions = tuple(
            session.scalars(
                select(ResearchWorkflowModelConsumption)
                .filter_by(
                    owner_id=owner_id,
                    workflow_id=root.id,
                    stage_id=stage.id,
                    attempt_id=terminal.attempt_id,
                    generation=terminal.generation,
                )
                .limit(2)
            )
        )
        if len(inclusions) != 1:
            fail()
        inclusion = inclusions[0]
        op = session.get(ResearchModelOperation, inclusion.operation_id)
        transport = session.get(ResearchModelTransport, inclusion.transport_id)
        if (
            op is None
            or transport is None
            or (
                inclusion.owner_id,
                inclusion.workflow_id,
                inclusion.stage_id,
                inclusion.run_id,
                inclusion.attempt_id,
                inclusion.generation,
                inclusion.input_revision_id,
                inclusion.input_sha256,
            )
            != (
                owner_id,
                root.id,
                stage.id,
                run.id,
                terminal.attempt_id,
                terminal.generation,
                stage.input_revision_id,
                config.workflow_inputs_sha256,
            )
            or (op.owner_id, op.run_id, op.attempt_id, op.generation, op.request_sha256)
            != (
                owner_id,
                run.id,
                terminal.attempt_id,
                terminal.generation,
                inclusion.request_sha256,
            )
            or (
                transport.owner_id,
                transport.run_id,
                transport.attempt_id,
                transport.generation,
                transport.operation_id,
                transport.ordinal,
            )
            != (owner_id, run.id, terminal.attempt_id, terminal.generation, op.id, 1)
        ):
            fail()
        requests = (
            StageConsumption(
                stage_id=stage.id,
                input_revision_id=stage.input_revision_id,
                input_sha256=binding.input_sha256,
                state="included_in_submitted_prompt",
                model_operation_id=op.id,
                transport_id=transport.id,
                request_sha256=op.request_sha256,
            ),
        )
    automatic_feedback = ()
    predecessor_id = None
    if stage.ordinal >= 3:
        from backend.app.research_workflow_rd_predecessor import (
            verify_rd_predecessor_metadata,
        )

        predecessor = verify_rd_predecessor_metadata(
            session, owner_id, root.id, stage.id
        )
        automatic_feedback = predecessor.feedback_checkpoint.outputs
        predecessor_id = predecessor.feedback_checkpoint_id
        if any(
            ref not in config.workflow_inputs.selection.refs
            for ref in automatic_feedback
        ):
            fail()
    statuses = {
        "queued",
        "preparing",
        "running",
        "finalizing",
        "pause_requested",
        "paused",
        "cancel_requested",
        "cancelled",
        "failed",
        "interrupted",
        "succeeded",
    }
    if run.status not in statuses:
        fail()
    untouched = (
        run.status == "queued"
        and not attempts
        and operation is None
        and evidence is None
    )
    return WorkflowStageHistory(
        stage_id=stage.id,
        ordinal=stage.ordinal,
        kind=stage.kind,
        input_revision_id=stage.input_revision_id,
        input_sha256=binding.input_sha256,
        definition_sha256=stage.definition_sha256,
        child_run_id=run.id,
        consumption="included_in_submitted_prompt"
        if requests
        else "selected_not_submitted"
        if untouched
        else "not_observed",
        checkpoint_id=checkpoint,
        source_status=run.status,
        child_attempt_id=None if current is None else current.id,
        started_at=None if current is None else getattr(current, "started_at", None),
        finished_at=None
        if terminal is None or current is None
        else current.finished_at,
        safe_error_code=None,
        input_refs=config.workflow_inputs.selection.refs,
        derived_input_refs=(),
        automatic_feedback_refs=automatic_feedback,
        predecessor_checkpoint_id=predecessor_id,
        output_refs=()
        if terminal is None
        else (
            SourceOutputRef(
                workflow_id=root.id,
                stage_id=stage.id,
                child_run_id=run.id,
                output_kind="direct_analysis",
                output_id=terminal.analysis_id,
                content_sha256=terminal.analysis_result_sha256,
            ),
        ),
        model_requests=requests,
        containment="not_observed" if terminal is None else "positive_stop",
        settlement="not_observed" if terminal is None else "settled",
    )
