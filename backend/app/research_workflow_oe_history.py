"""Actual structured workflow child history; no inferred consumption or release."""

from sqlalchemy import select

from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_saved import verify_workflow_oe_submission
from backend.app.research_broker_models import ResearchModelOperation
from backend.app.research_workflow_input_reads import retained_input_guard
from backend.app.research_workflow_models import (
    ResearchWorkflowCheckpoint,
    ResearchWorkflowModelConsumption,
)
from backend.app.research_workflow_oe_checkpoint import verify_oe_checkpoint
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_inputs.contracts import SourceOutputRef
from packages.research_workflow.contracts import StageConsumption
from packages.research_workflow.oe_history import WorkflowOEStageHistory


def fail():
    raise DomainError(
        "workflow_child_history_changed", "Retained structured child graph differs", 409
    )


def read_oe_stage_history(session, owner_id, root, stage, settings):
    row = session.scalar(
        select(OEWorkflowSubmission).filter_by(
            owner_id=owner_id, workflow_id=root.id, stage_id=stage.id
        )
    )
    if row is None:
        fail()
    with retained_input_guard(settings):
        row = verify_workflow_oe_submission(
            session, owner_id, row.submission_id, settings
        )
    run = session.scalar(
        select(ResearchRun).filter_by(owner_id=owner_id, id=row.run_id)
    )
    if run is None or run.id != stage.child_run_id:
        fail()
    config = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(run.config)
    )
    ctx = config.workflow_context
    binding = ctx.inputs.binding
    attempts = tuple(
        session.scalars(select(ExecutionAttempt).filter_by(run_id=run.id).limit(2))
    )
    if len(attempts) > 1:
        fail()
    current = attempts[0] if attempts else None
    if current is not None and (current.id, current.generation) != (
        run.active_attempt_id,
        run.generation,
    ):
        fail()
    checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint.id).filter_by(
            owner_id=owner_id, workflow_id=root.id, stage_id=stage.id
        )
    )
    terminal = None
    if checkpoint is not None:
        with retained_input_guard(settings):
            _, terminal = verify_oe_checkpoint(
                session, owner_id, root.id, checkpoint, settings
            )
    operation = session.scalar(
        select(ResearchModelOperation.id)
        .filter_by(owner_id=owner_id, run_id=run.id)
        .limit(1)
    )
    evidence = tuple(
        session.scalars(
            select(ResearchWorkflowModelConsumption)
            .filter_by(owner_id=owner_id, workflow_id=root.id, stage_id=stage.id)
            .limit(3)
        )
    )
    if len(evidence) > 2:
        fail()
    requests = ()
    if terminal is not None:
        # Actual full terminal reader has rechecked every final rendered request,
        # physical receipt and settlement. A standalone row does not confer this.
        requests = tuple(
            StageConsumption(
                stage_id=stage.id,
                input_revision_id=stage.input_revision_id,
                input_sha256=binding.input_sha256,
                state="included_in_submitted_prompt",
                model_operation_id=item.operation_id,
                transport_id=item.transport_id,
                request_sha256=item.request_sha256,
            )
            for item in sorted(evidence, key=lambda item: str(item.id))
        )
    untouched = (
        run.status == "queued" and not attempts and operation is None and not evidence
    )
    return WorkflowOEStageHistory(
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
        safe_error_code="workflow_stage_failed"
        if run.status in {"failed", "cancelled", "interrupted"}
        else None,
        input_refs=ctx.inputs.selection.refs,
        derived_input_refs=(),
        automatic_feedback_refs=(),
        predecessor_checkpoint_id=ctx.rd_checkpoint_id,
        output_refs=()
        if terminal is None
        else tuple(
            SourceOutputRef(
                workflow_id=root.id,
                stage_id=stage.id,
                child_run_id=run.id,
                output_kind="structured_execution",
                output_id=item.execution_id,
                content_sha256=item.result_sha256,
            )
            for item in terminal.executions
        ),
        model_requests=requests,
        containment="not_observed" if terminal is None else "positive_stop",
        settlement="not_observed" if terminal is None else "settled",
    )
