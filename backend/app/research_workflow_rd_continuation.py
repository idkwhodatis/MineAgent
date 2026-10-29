"""Atomic continuation: actual released feedback, latest selection, NEW child."""

from uuid import uuid4

from sqlalchemy import func, select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.models import utcnow
from backend.app.research_input_resolution import resolve_model_input_versions
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_history import read_selected_inputs
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowCommand,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_native_guard import native_guard_from_publication
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_predecessor import verify_rd_predecessor
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.feedback_checkpoint import FeedbackCheckpoint
from packages.research_workflow.rd_claim import WorkflowRDClaim, WorkflowRDClaimReceipt
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)


def fail(code="workflow_rd_claim_changed"):
    raise DomainError(code, "Workflow child claim is unavailable or changed", 409)


def claim_next_rd_stage(session, owner_id, command, settings, *, admission):
    """Flush stage, actual queued child and original command receipt atomically.

    Caller must roll back on error. The existing publication guard owns commit;
    accepted replay returns original IDs, not a claim about the child's current
    status. Paused/cancelled stages cannot be silently resumed here.
    """
    command = WorkflowRDClaim.model_validate_json(canonical_bytes(command))
    require_schema(session)
    require_publication_admission(session, settings, admission)
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
    raw, sha = canonical_bytes(command).decode(), digest(command)
    if prior is not None:
        if (
            prior.owner_id,
            prior.workflow_id,
            prior.command_canonical,
            prior.command_sha256,
        ) != (owner_id, root.id, raw, sha):
            fail("workflow_command_conflict")
        try:
            receipt = WorkflowRDClaimReceipt.model_validate_json(prior.result_canonical)
            if canonical_bytes(receipt).decode() != prior.result_canonical or (
                receipt.workflow_id,
                receipt.command_id,
                receipt.command_sha256,
            ) != (root.id, prior.id, sha):
                fail()
        except (TypeError, ValueError):
            fail()
        from backend.app.rdagent_workflow_saved import (
            verify_workflow_submission_under_native_guard,
        )

        with native_guard_from_publication(session, settings, admission=admission):
            child = verify_workflow_submission_under_native_guard(
                session, owner_id, receipt.submission_id, settings
            )
        if (child.owner_id, child.workflow_id, child.stage_id, child.run_id) != (
            owner_id,
            root.id,
            receipt.stage_id,
            receipt.child_run_id,
        ):
            fail()
        require_publication_admission(session, settings, admission)
        return receipt
    if (
        root.revision != command.expected_revision
        or root.current_input_revision_id != command.input_revision_id
    ):
        fail("workflow_revision_conflict")
    if root.status not in {"ready", "blocked"} or (
        root.status == "blocked"
        and root.blocked_reason != "workflow_next_child_pending"
    ):
        fail()
    if len(root.plan_canonical.encode()) > 1048576:
        fail()
    try:
        plan = read_guidance_plan(session, root.plan_canonical)
    except (TypeError, ValueError):
        fail()
    if (plan.owner_id, plan.sha256, canonical_bytes(plan).decode(), plan.mode) != (
        owner_id,
        root.plan_sha256,
        root.plan_canonical,
        root.mode,
    ):
        fail()
    if root.budget_canonical != canonical_bytes(
        plan.budget_ceiling
    ).decode() or root.budget_sha256 != digest(plan.budget_ceiling):
        fail()
    stages = tuple(
        session.scalars(
            select(ResearchWorkflowStage)
            .where(
                ResearchWorkflowStage.owner_id == owner_id,
                ResearchWorkflowStage.workflow_id == root.id,
            )
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(18)
        )
    )
    schedule = stage_schedule(plan)
    if (
        not stages
        or len(stages) >= len(schedule)
        or len(stages) > 16
        or stages[-1].id != root.current_stage_id
        or stages[-1].kind != "experiment_feedback_projection"
        or schedule[len(stages)][0] != "rd_factor_generation"
    ):
        fail()
    for ordinal, previous in enumerate(stages, 1):
        if (
            previous.owner_id,
            previous.workflow_id,
            previous.ordinal,
            previous.kind,
        ) != (owner_id, root.id, ordinal, schedule[ordinal - 1][0]):
            fail()
    checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=root.id,
            stage_id=stages[-1].id,
            id=root.latest_checkpoint_id,
        )
    )
    if checkpoint is None:
        fail()
    facts = FeedbackCheckpoint.model_validate_json(checkpoint.terminal_canonical)
    if (
        canonical_bytes(facts).decode() != checkpoint.terminal_canonical
        or digest(facts) != checkpoint.terminal_sha256
        or facts.state != "succeeded"
        or (facts.owner_id, facts.workflow_id, facts.stage_id)
        != (owner_id, root.id, stages[-1].id)
    ):
        fail()
    selection = read_selected_inputs(session, owner_id, root.id)
    if selection.revision_id != command.input_revision_id:
        fail()
    # Scheduled derived feedback is NOT a user selection or release. The exact
    # preceding checkpoint is committed in this child's immutable definition.
    # Preserve user-selected manual/external order; replace earlier auto feedback.
    refs = (
        tuple(
            ref
            for ref in selection.refs
            if ref.origin != "experiment_feedback_proposal"
        )
        + facts.outputs
    )
    frozen = WorkflowInputSnapshot(
        owner_id=owner_id, workflow_id=root.id, revision_id=uuid4(), refs=refs
    )
    ordinal = session.scalar(
        select(func.max(ResearchWorkflowRevision.ordinal)).where(
            ResearchWorkflowRevision.owner_id == owner_id,
            ResearchWorkflowRevision.workflow_id == root.id,
        )
    )
    if type(ordinal) is not int or not 1 <= ordinal < 1000000:
        fail()
    session.add(
        ResearchWorkflowRevision(
            id=frozen.revision_id,
            owner_id=owner_id,
            workflow_id=root.id,
            ordinal=ordinal + 1,
            inputs_canonical=canonical_bytes(frozen).decode(),
            inputs_sha256=frozen.sha256,
        )
    )
    iteration = schedule[len(stages)][1]
    total = plan.research_template.budget
    for previous in stages:
        allocation = BudgetVector.model_validate_json(previous.allocation_canonical)
        if (
            canonical_bytes(allocation).decode() != previous.allocation_canonical
            or digest(allocation) != previous.allocation_sha256
        ):
            fail()
        total = total.add(allocation)
    remaining = plan.maximum_iterations - iteration - 1
    future = BudgetVector(
        **{
            key: getattr(plan.research_template.budget, key) * remaining
            for key in BudgetVector.model_fields
        }
    )
    future = future.add(BudgetVector(storage_bytes=65536 * remaining))
    from packages.research_workflow.structured_plan import terminal_structured_budget

    future = future.add(terminal_structured_budget(plan))
    if not plan.budget_ceiling.covers(total.add(future)):
        fail("workflow_budget_exhausted")
    rd_checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=root.id,
            id=facts.rd_checkpoint_id,
            stage_id=facts.source.stage_id,
        )
    )
    from packages.research_workflow.rd_checkpoint import RDStageCheckpoint

    if rd_checkpoint is None:
        fail()
    parent = RDStageCheckpoint.model_validate_json(rd_checkpoint.terminal_canonical)
    # Full checkpoint/source/physical verification occurs after the exact NEW
    # stage exists, in the same uncommitted transaction; errors roll back all.
    definition = RDStageDefinition(
        plan_sha256=plan.sha256,
        research_iteration=iteration,
        predecessor_checkpoint_id=checkpoint.id,
        parent_factor_version_id=parent.factor_version_id,
        parent_source_stage_id=parent.stage_id,
    )
    stage = ResearchWorkflowStage(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=root.id,
        ordinal=len(stages) + 1,
        input_revision_id=frozen.revision_id,
        kind="rd_factor_generation",
        child_run_id=uuid4(),
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=definition.sha256,
        allocation_canonical=canonical_bytes(plan.research_template.budget).decode(),
        allocation_sha256=digest(plan.research_template.budget),
    )
    session.add(stage)
    root.current_input_revision_id = frozen.revision_id
    root.current_stage_id, root.status, root.blocked_reason = stage.id, "running", None
    root.revision += 1
    root.updated_at = utcnow()
    session.flush()
    with native_guard_from_publication(session, settings, admission=admission):
        verify_rd_predecessor(session, owner_id, root.id, stage.id, settings)
        resolve_model_input_versions(session, owner_id, frozen.refs, settings)
    from backend.app.rdagent_workflow_submission import submit_workflow_child

    child = submit_workflow_child(
        session, owner_id, root.id, stage.id, settings, admission=admission
    )
    if (child.owner_id, child.workflow_id, child.stage_id, child.run_id) != (
        owner_id,
        root.id,
        stage.id,
        stage.child_run_id,
    ):
        fail()
    command_id = uuid4()
    receipt = WorkflowRDClaimReceipt(
        workflow_id=root.id,
        command_id=command_id,
        command_sha256=sha,
        stage_id=stage.id,
        child_run_id=child.run_id,
        submission_id=child.id,
        revision=root.revision,
    )
    session.add(
        ResearchWorkflowCommand(
            id=command_id,
            owner_id=owner_id,
            workflow_id=root.id,
            idempotency_key=command.idempotency_key,
            command_canonical=raw,
            command_sha256=sha,
            result_canonical=canonical_bytes(receipt).decode(),
        )
    )
    require_publication_admission(session, settings, admission)
    session.flush()
    return receipt


def dispatch_next_rd_stage(factory, settings, *, owner_id, command):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            receipt = claim_next_rd_stage(
                session, owner_id, command, settings, admission=admission
            )
        return receipt  # ACK only after quota-owned successful commit.
