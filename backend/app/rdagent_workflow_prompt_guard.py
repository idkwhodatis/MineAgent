"""028 first generation must actually submit the immutable combined-input text."""

from sqlalchemy import select

from backend.app.rdagent_seed_services import resolve_seed_parent
from backend.app.rdagent_workflow_capability import require_workflow_revision
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_saved import verify_workflow_submission
from backend.app.research_lifecycle import require_neutral_transaction
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.rdagent_adapter.workflow_prompt import first_workflow_messages


def require_first_workflow_prompt(session, fence, call, request, config, settings):
    require_workflow_revision(session)
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if (
        type(config) is not WorkflowFactorResearchConfig
        or call.phase != "generation"
        or call.ordinal != 0
        or request.kind != "chat"
    ):
        raise ValueError("Exact workflow first generation required")
    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(
            owner_id=fence.owner_id, run_id=fence.run_id
        )
    )
    if row is None:
        raise ValueError("Actual workflow receipt required")
    verified = verify_workflow_submission(session, fence.owner_id, row.id, settings)
    if verified.run_id != fence.run_id or verified.config_sha256 != config.sha256:
        raise ValueError("Workflow request receipt differs")
    seed = (
        None
        if config.workflow_parent is None
        else resolve_seed_parent(
            session, fence.owner_id, config.workflow_parent.factor_version_id, settings
        )
    )
    if seed is not None and seed.reference != config.workflow_parent:
        raise ValueError("Actual workflow parent differs")
    system, user = first_workflow_messages(config, seed)
    if tuple((item.role, item.content) for item in request.messages) != (
        ("system", system),
        ("user", user),
    ):
        raise ValueError(
            "First workflow request does not consume exact combined inputs"
        )
    return verified


def bind_workflow_consumption(
    session,
    fence,
    *,
    call,
    member,
    operation,
    transport,
    request,
    config,
    settings,
    replay=False,
):
    """Only the actual first generation envelope proves inclusion, not compliance."""
    if type(replay) is not bool:
        raise ValueError("Explicit consumption replay mode required")
    if call.phase != "generation" or call.ordinal != 0 or member.request_ordinal != 0:
        return None
    if member.relation != "initial":
        raise ValueError("Workflow first request relation differs")
    verified = require_first_workflow_prompt(
        session, fence, call, request, config, settings
    )
    from uuid import uuid4

    from backend.app.research_workflow_models import ResearchWorkflowModelConsumption
    from packages.rdagent_provider.codec import request_sha256

    if (
        member.operation_id != operation.id
        or member.call_id != call.id
        or transport.operation_id != operation.id
        or transport.ordinal != 1
        or operation.request_sha256 != request_sha256(operation.protocol, request)
    ):
        raise ValueError("Workflow consumption operation chain differs")
    values = {
        "owner_id": fence.owner_id,
        "workflow_id": verified.workflow_id,
        "stage_id": verified.stage_id,
        "input_revision_id": verified.input_revision_id,
        "input_sha256": config.workflow_inputs_sha256,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": fence.generation,
        "operation_id": operation.id,
        "transport_id": transport.id,
        "request_sha256": operation.request_sha256,
    }
    old = session.scalar(
        select(ResearchWorkflowModelConsumption).filter_by(
            stage_id=verified.stage_id, operation_id=operation.id
        )
    )
    if old is not None:
        if any(getattr(old, key) != value for key, value in values.items()):
            raise ValueError("Workflow consumption immutable replay differs")
        return old
    if replay:
        raise ValueError("Atomic workflow admission lacks original consumption row")
    row = ResearchWorkflowModelConsumption(id=uuid4(), **values)
    session.add(row)
    session.flush()
    return row
