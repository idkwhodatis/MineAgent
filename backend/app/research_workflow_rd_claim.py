"""Atomic initial stage/new-child allocation, still gated on explicit028 install."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.models import utcnow
from backend.app.research_input_resolution import resolve_model_input_versions
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_history import read_selected_inputs
from backend.app.research_workflow_local_checkpoint import observe_external_terminal
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCommand,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_native_guard import native_guard_from_publication
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.rd_claim import WorkflowRDClaim, WorkflowRDClaimReceipt
from packages.research_workflow.rd_stage import RDStageDefinition


def fail(code="workflow_rd_claim_changed"):
    raise DomainError(code, "Workflow child claim is unavailable or changed", 409)


def claim_initial_rd_stage(session, owner_id, command, settings, *, admission):
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
        and root.blocked_reason != "workflow_child_adapter_unavailable"
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
            .limit(3)
        )
    )
    checkpoint_id = None
    if plan.normalize_external_first:
        if (
            len(stages) != 1
            or stages[0].id != root.current_stage_id
            or stages[0].kind != "external_normalization"
            or root.latest_checkpoint_id is None
        ):
            fail()
        checkpoint_id, facts = observe_external_terminal(
            session,
            owner_id,
            root.id,
            stages[0].id,
            root.revision,
            settings,
            admission=admission,
        )
        if checkpoint_id != root.latest_checkpoint_id or facts.state != "succeeded":
            fail()
    elif (
        stages
        or root.current_stage_id is not None
        or root.latest_checkpoint_id is not None
    ):
        fail()
    selection = read_selected_inputs(session, owner_id, root.id)
    if selection.revision_id != command.input_revision_id:
        fail()
    with native_guard_from_publication(session, settings, admission=admission):
        resolve_model_input_versions(session, owner_id, selection.refs, settings)
    definition = RDStageDefinition(
        plan_sha256=plan.sha256,
        research_iteration=0,
        predecessor_checkpoint_id=checkpoint_id,
        parent_factor_version_id=plan.initial_parent_factor_version_id,
        parent_source_stage_id=None,
    )
    stage = ResearchWorkflowStage(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=root.id,
        ordinal=len(stages) + 1,
        input_revision_id=selection.revision_id,
        kind="rd_factor_generation",
        child_run_id=uuid4(),
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=definition.sha256,
        allocation_canonical=canonical_bytes(plan.research_template.budget).decode(),
        allocation_sha256=digest(plan.research_template.budget),
    )
    session.add(stage)
    root.current_stage_id, root.status, root.blocked_reason = stage.id, "running", None
    root.revision += 1
    root.updated_at = utcnow()
    session.flush()
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


def dispatch_initial_rd_stage(factory, settings, *, owner_id, command):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            receipt = claim_initial_rd_stage(
                session, owner_id, command, settings, admission=admission
            )
        return receipt  # ACK only after quota-owned successful commit.
