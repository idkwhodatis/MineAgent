"""Actual workflow messages and atomic submitted-transport consumption evidence."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_saved import verify_workflow_oe_submission
from backend.app.research_workflow_models import ResearchWorkflowModelConsumption
from packages.openevolve_structured.workflow_prompt import validate_workflow_rendered
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig


def require_workflow_prompt(session, fence, config, rendered, settings):
    if type(config) is not WorkflowStructuredRunConfig:
        raise ValueError("Exact workflow OE config required")
    row = session.scalar(
        select(OEWorkflowSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if row is None:
        raise ValueError("Actual workflow OE submission required")
    verified = verify_workflow_oe_submission(
        session, fence.owner_id, row.submission_id, settings
    )
    if verified.config_sha256 != config.sha256 or verified.run_id != fence.run_id:
        raise ValueError("Workflow OE frozen request differs")
    validate_workflow_rendered(config.workflow_context, rendered.system, rendered.user)
    return verified


def bind_workflow_consumption(
    session, fence, config, member, operation, transport, request
):
    """Called only after exact rendered operation validation, in its admission TX."""
    if type(config) is not WorkflowStructuredRunConfig:
        raise ValueError("Exact workflow OE config required")
    context = config.workflow_context
    binding = context.inputs.binding
    if (
        context.child_run_id != fence.run_id
        or binding.owner_id != fence.owner_id
        or operation.request_sha256 != request.sha256
        or member.request_sha256 != request.sha256
        or member.operation_id != operation.id
        or member.transport_id != transport.id
        or transport.operation_id != operation.id
        or transport.ordinal != 1
        or transport.state != "submitted"
        or operation.request != request.model_dump(mode="json")
    ):
        raise ValueError("Actual workflow OE transport chain differs")
    for row in (member, operation, transport):
        if any(
            getattr(row, key) != getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise ValueError("Actual workflow OE operation scope differs")
    validate_workflow_rendered(
        context, request.messages[0].content, request.messages[1].content
    )
    values = {
        "owner_id": fence.owner_id,
        "workflow_id": binding.workflow_id,
        "stage_id": binding.stage_id,
        "input_revision_id": binding.input_revision_id,
        "input_sha256": context.inputs.sha256,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": fence.generation,
        "operation_id": operation.id,
        "transport_id": transport.id,
        "request_sha256": request.sha256,
    }
    old = session.scalar(
        select(ResearchWorkflowModelConsumption).filter_by(
            stage_id=binding.stage_id, operation_id=operation.id
        )
    )
    if old is not None:
        if any(getattr(old, key) != value for key, value in values.items()):
            raise ValueError("Workflow OE consumption replay differs")
        return old
    row = ResearchWorkflowModelConsumption(id=uuid4(), **values)
    session.add(row)
    session.flush()
    return row
