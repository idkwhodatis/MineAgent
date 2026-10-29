"""Bounded deterministic workflow driver, not a second engine worker/scheduler.

Existing child outbox/worker owns all RD execution. This tick only runs actual
local derivations and atomically queues new children after actual checkpoint,
release and predecessor checks. Unknown started local work is excluded, never
adopted. Actionable roots precede unresolved evidence waits; this bounded
best-effort ordering is not a strong fairness guarantee under continuous load.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, exists, select
from sqlalchemy.orm import aliased

from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import ResearchRun
from backend.app.research_input_dispatch import run_external_stage
from backend.app.research_input_feedback import run_feedback_stage
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputDerivationEvent,
)
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_feedback_checkpoint import checkpoint_feedback_stage
from backend.app.research_workflow_feedback_stage import dispatch_feedback_stage
from backend.app.research_workflow_local_checkpoint import checkpoint_external_stage
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.research_workflow_rd_checkpoint import checkpoint_rd_stage
from backend.app.research_workflow_rd_claim import dispatch_initial_rd_stage
from backend.app.research_workflow_rd_continuation import dispatch_next_rd_stage
from backend.app.services import DomainError
from packages.research_workflow.rd_claim import WorkflowRDClaim


@dataclass(frozen=True)
class TickOutcome:
    workflow_id: UUID
    state: str  # Finite internal diagnostics only; never provider/source output.


def _read(factory, settings, owner_id, workflow_id):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        require_schema(session)
        root = session.scalar(
            select(ResearchWorkflow).filter_by(owner_id=owner_id, id=workflow_id)
        )
        if root is None:
            raise DomainError("not_found", "Workflow not found", 404)
        stage = (
            None
            if root.current_stage_id is None
            else session.scalar(
                select(ResearchWorkflowStage).filter_by(
                    owner_id=owner_id, workflow_id=workflow_id, id=root.current_stage_id
                )
            )
        )
        if root.current_stage_id is not None and stage is None:
            raise DomainError(
                "workflow_graph_inconsistent", "Workflow stage differs", 409
            )
        return (
            root.revision,
            root.status,
            root.blocked_reason,
            root.current_input_revision_id,
            root.current_stage_id,
            None if stage is None else stage.kind,
        )


def drive_one(factory, settings, *, owner_id, workflow_id):
    revision, status, reason, selection, stage_id, kind = _read(
        factory, settings, owner_id, workflow_id
    )
    if kind == "external_normalization" and status in {
        "ready",
        "running",
        "pause_requested",
        "cancel_requested",
    }:
        run_external_stage(
            factory,
            settings,
            owner_id=owner_id,
            workflow_id=workflow_id,
            stage_id=stage_id,
        )
        revision, status, reason, selection, current_id, kind = _read(
            factory, settings, owner_id, workflow_id
        )
        if current_id != stage_id:
            return TickOutcome(workflow_id, "changed")
        checkpoint_external_stage(
            factory,
            settings,
            owner_id=owner_id,
            workflow_id=workflow_id,
            stage_id=stage_id,
            expected_revision=revision,
        )
        revision, status, reason, selection, stage_id, kind = _read(
            factory, settings, owner_id, workflow_id
        )
    if kind == "experiment_feedback_projection" and status in {
        "ready",
        "running",
        "pause_requested",
        "cancel_requested",
    }:
        try:
            run_feedback_stage(
                factory,
                settings,
                owner_id=owner_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
            )
        except DomainError as exc:
            if exc.code == "workflow_feedback_release_required":
                _record_wait(
                    factory,
                    settings,
                    owner_id,
                    workflow_id,
                    stage_id,
                    revision,
                    "workflow_feedback_release_required",
                )
                return TickOutcome(workflow_id, "waiting_release")
            raise
        revision, status, reason, selection, current_id, kind = _read(
            factory, settings, owner_id, workflow_id
        )
        if current_id != stage_id:
            return TickOutcome(workflow_id, "changed")
        checkpoint_feedback_stage(
            factory,
            settings,
            owner_id=owner_id,
            workflow_id=workflow_id,
            stage_id=stage_id,
            expected_revision=revision,
        )
        revision, status, reason, selection, stage_id, kind = _read(
            factory, settings, owner_id, workflow_id
        )
    if kind == "structured_factor_execution" and status in {
        "running",
        "blocked",
        "pause_requested",
        "cancel_requested",
    }:
        from backend.app.research_workflow_oe_checkpoint import checkpoint_oe_stage

        try:
            checkpoint_oe_stage(
                factory,
                settings,
                owner_id=owner_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
                expected_revision=revision,
            )
        except DomainError as exc:
            if exc.code == "workflow_waiting_child":
                return TickOutcome(workflow_id, "child_owned_by_engine_worker")
            if exc.code == "workflow_waiting_containment":
                return TickOutcome(workflow_id, "workflow_waiting_containment")
            raise
        return TickOutcome(workflow_id, "structured_stage_closed")
    if kind == "rd_factor_generation" and status in {
        "ready",
        "running",
        "blocked",
        "pause_requested",
        "cancel_requested",
    }:
        try:
            checkpoint_rd_stage(
                factory,
                settings,
                owner_id=owner_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
                expected_revision=revision,
            )
        except DomainError as exc:
            if exc.code == "workflow_waiting_child":
                diagnostic = _record_child_terminal_diagnostic(
                    factory, settings, owner_id, workflow_id, stage_id, revision
                )
                return TickOutcome(
                    workflow_id, diagnostic or "child_owned_by_engine_worker"
                )
            if exc.code == "workflow_waiting_containment":
                _record_containment_wait(
                    factory, settings, owner_id, workflow_id, stage_id, revision
                )
                return TickOutcome(workflow_id, "workflow_waiting_containment")
            raise
        revision, status, reason, selection, current_id, kind = _read(
            factory, settings, owner_id, workflow_id
        )
        if current_id != stage_id:
            return TickOutcome(workflow_id, "changed")
        if status == "completed":
            return TickOutcome(workflow_id, "completed")
        if status == "blocked" and reason in {
            "workflow_structured_pending",
            "workflow_structured_release_required",
        }:
            from backend.app.research_workflow_oe_claim import dispatch_oe_stage
            from packages.research_workflow.oe_stage import WorkflowOEClaim

            command = WorkflowOEClaim(
                workflow_id=workflow_id,
                expected_revision=revision,
                input_revision_id=selection,
                idempotency_key=f"auto-oe:{workflow_id}:{selection}:{revision}",
            )
            try:
                dispatch_oe_stage(factory, settings, owner_id=owner_id, command=command)
            except DomainError as exc:
                if exc.code != "workflow_structured_release_required":
                    raise
                _record_wait(
                    factory,
                    settings,
                    owner_id,
                    workflow_id,
                    stage_id,
                    revision,
                    "workflow_structured_release_required",
                )
                return TickOutcome(workflow_id, "waiting_release")
            return TickOutcome(workflow_id, "structured_child_queued")
        if status == "blocked" and reason == "workflow_feedback_pending":
            dispatch_feedback_stage(
                factory,
                settings,
                owner_id=owner_id,
                workflow_id=workflow_id,
                expected_revision=revision,
            )
            return TickOutcome(workflow_id, "feedback_allocated")
    if status == "paused":
        return TickOutcome(workflow_id, "paused")
    if status in {"cancelled", "cancel_requested", "pause_requested"}:
        return TickOutcome(workflow_id, "control_pending_or_terminal")
    if kind == "rd_factor_generation":
        return TickOutcome(workflow_id, "child_owned_by_engine_worker")
    if kind == "experiment_feedback_projection":
        if status == "ready" or (
            status == "blocked" and reason == "workflow_next_child_pending"
        ):
            command = WorkflowRDClaim(
                workflow_id=workflow_id,
                expected_revision=revision,
                input_revision_id=selection,
                idempotency_key=f"auto-next-rd:{workflow_id}:{selection}:{revision}",
            )
            dispatch_next_rd_stage(
                factory, settings, owner_id=owner_id, command=command
            )
            return TickOutcome(workflow_id, "child_queued")
        return TickOutcome(workflow_id, "blocked")
    if status == "ready" or (
        status == "blocked" and reason == "workflow_child_adapter_unavailable"
    ):
        command = WorkflowRDClaim(
            workflow_id=workflow_id,
            expected_revision=revision,
            input_revision_id=selection,
            idempotency_key=f"auto-rd:{workflow_id}:{selection}:{revision}",
        )
        dispatch_initial_rd_stage(factory, settings, owner_id=owner_id, command=command)
        return TickOutcome(workflow_id, "child_queued")
    return TickOutcome(workflow_id, "blocked")


def tick_workflows(factory, settings, *, limit=4):
    """At most four roots; every external action rechecks actual locks/CAS.

    Caller invokes outside an existing SQL transaction. This function is not
    activated on old revisions and rejects every uninstalled capability.
    """
    if type(limit) is not int or not 1 <= limit <= 4:
        raise ValueError("Workflow tick limit must be between one and four")
    event = ResearchInputDerivationEvent
    terminal = aliased(ResearchInputDerivationEvent)
    unresolved = exists(
        select(ResearchInputDerivation.id)
        .join(event, event.derivation_id == ResearchInputDerivation.id)
        .where(
            ResearchInputDerivation.stage_id == ResearchWorkflowStage.id,
            event.state == "started",
            ~exists(
                select(terminal.id).where(
                    terminal.derivation_id == ResearchInputDerivation.id,
                    terminal.state.in_(
                        ("succeeded", "rejected", "failed", "cancelled", "interrupted")
                    ),
                )
            ),
        )
    )
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        require_schema(session)
        rows = tuple(
            session.execute(
                select(ResearchWorkflow.owner_id, ResearchWorkflow.id)
                .outerjoin(
                    ResearchWorkflowStage,
                    ResearchWorkflow.current_stage_id == ResearchWorkflowStage.id,
                )
                .where(
                    (ResearchWorkflowStage.id.is_(None))
                    | (
                        ResearchWorkflowStage.kind.in_(
                            ("external_normalization", "experiment_feedback_projection")
                        )
                    )
                    | (
                        (
                            ResearchWorkflowStage.kind.in_(
                                ("rd_factor_generation", "structured_factor_execution")
                            )
                        )
                        & exists(
                            select(ResearchRun.id).where(
                                ResearchRun.id == ResearchWorkflowStage.child_run_id,
                                ResearchRun.owner_id == ResearchWorkflow.owner_id,
                                ResearchRun.status.in_(
                                    ("succeeded", "failed", "cancelled")
                                ),
                            )
                        )
                    ),
                    ResearchWorkflow.status.in_(
                        (
                            "ready",
                            "running",
                            "pause_requested",
                            "cancel_requested",
                            "blocked",
                        )
                    ),
                    (ResearchWorkflow.status != "blocked")
                    | (
                        ResearchWorkflow.blocked_reason.in_(
                            (
                                "workflow_child_adapter_unavailable",
                                "workflow_feedback_pending",
                                "workflow_structured_pending",
                                "workflow_structured_release_required",
                                "workflow_waiting_containment",
                                "workflow_feedback_release_required",
                                "workflow_next_child_pending",
                                "workflow_child_failed_unverified",
                                "workflow_child_cancelled_unverified",
                            )
                        )
                    ),
                    ~unresolved,
                )
                .order_by(
                    # Unresolved evidence must remain retryable, but must not
                    # occupy every bounded slot ahead of newly actionable work.
                    # A control request takes priority even if its diagnostic
                    # still records an earlier release/containment wait. Terminal
                    # child diagnostics cannot advance even under cancellation.
                    case(
                        (
                            ResearchWorkflow.blocked_reason.in_(
                                (
                                    "workflow_child_failed_unverified",
                                    "workflow_child_cancelled_unverified",
                                )
                            ),
                            2,
                        ),
                        (
                            ResearchWorkflow.status.in_(
                                ("pause_requested", "cancel_requested")
                            ),
                            0,
                        ),
                        (
                            ResearchWorkflow.blocked_reason.in_(
                                (
                                    "workflow_feedback_release_required",
                                    "workflow_structured_release_required",
                                    "workflow_waiting_containment",
                                    "workflow_child_failed_unverified",
                                    "workflow_child_cancelled_unverified",
                                )
                            ),
                            2,
                        ),
                        else_=1,
                    ),
                    ResearchWorkflow.created_at,
                    ResearchWorkflow.id,
                )
                .limit(limit)
            )
        )
    outcomes = []
    for owner_id, workflow_id in rows:
        try:
            outcomes.append(
                drive_one(factory, settings, owner_id=owner_id, workflow_id=workflow_id)
            )
        except DomainError as exc:
            # Preserve committed source/child records. Never invent an interrupted
            # terminal or reset a started receipt in response to an uncertain ACK.
            state = (
                "changed"
                if exc.code
                in {"workflow_revision_conflict", "workflow_command_conflict"}
                else "blocked"
            )
            outcomes.append(TickOutcome(workflow_id, state))
    return tuple(outcomes)


def _record_containment_wait(
    factory, settings, owner_id, workflow_id, stage_id, revision
):
    return _record_wait(
        factory,
        settings,
        owner_id,
        workflow_id,
        stage_id,
        revision,
        "workflow_waiting_containment",
    )


def _record_wait(factory, settings, owner_id, workflow_id, stage_id, revision, reason):
    from backend.app.research_lifecycle import _try_neutral_lock

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory.begin() as session,
    ):
        require_schema(session)
        _try_neutral_lock(session, f"submit:{owner_id}")
        root = session.scalar(
            select(ResearchWorkflow)
            .filter_by(owner_id=owner_id, id=workflow_id)
            .with_for_update(nowait=True)
        )
        if (
            root is not None
            and root.current_stage_id == stage_id
            and root.revision == revision
            and root.status
            in {"ready", "running", "blocked", "pause_requested", "cancel_requested"}
            and root.blocked_reason != reason
        ):
            # Diagnostic only: preserve control requests and the actual child.
            # The installed root trigger requires a CAS revision for *every*
            # changed row, including diagnostics. Reobserving the same wait is
            # read-only, so it neither invalidates controls nor churns revisions.
            root.revision += 1
            root.blocked_reason = reason
            session.flush()


def _record_child_terminal_diagnostic(
    factory, settings, owner_id, workflow_id, stage_id, revision
):
    """Observe terminal status only; never establish containment or a checkpoint.

    This is a metadata block under current-root CAS. A failed/cancelled child
    cannot authorize feedback, release, another child, or automatic retry.
    """
    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from backend.app.rdagent_workflow_saved import verify_workflow_submission_metadata
    from backend.app.research_lifecycle import _try_neutral_lock

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory.begin() as session,
    ):
        require_schema(session)
        _try_neutral_lock(session, f"submit:{owner_id}")
        root = session.scalar(
            select(ResearchWorkflow)
            .filter_by(owner_id=owner_id, id=workflow_id)
            .with_for_update(nowait=True)
        )
        if (
            root is None
            or root.owner_id != owner_id
            or root.current_stage_id != stage_id
            or root.revision != revision
            or root.status
            not in {
                "ready",
                "running",
                "blocked",
                "pause_requested",
                "cancel_requested",
            }
        ):
            return None
        stage = session.scalar(
            select(ResearchWorkflowStage).filter_by(
                owner_id=owner_id, workflow_id=workflow_id, id=stage_id
            )
        )
        if (
            stage is None
            or (stage.owner_id, stage.workflow_id, stage.id)
            != (owner_id, workflow_id, stage_id)
            or stage.kind != "rd_factor_generation"
            or stage.child_run_id is None
        ):
            raise DomainError(
                "workflow_graph_inconsistent", "Current child scope differs", 409
            )
        run = session.scalar(
            select(ResearchRun)
            .filter_by(owner_id=owner_id, id=stage.child_run_id)
            .with_for_update(nowait=True)
        )
        if run is None or (run.owner_id, run.id, run.engine, run.purpose) != (
            owner_id,
            stage.child_run_id,
            "rdagent",
            "factor_research",
        ):
            raise DomainError(
                "workflow_graph_inconsistent", "Current child identity differs", 409
            )
        if run.status not in {"failed", "cancelled"}:
            return None
        receipt = session.scalar(
            select(FactorWorkflowSubmission).filter_by(
                owner_id=owner_id,
                workflow_id=workflow_id,
                stage_id=stage_id,
                run_id=run.id,
            )
        )
        if receipt is None:
            raise DomainError(
                "workflow_graph_inconsistent", "Current child receipt missing", 409
            )
        receipt = verify_workflow_submission_metadata(session, owner_id, receipt.id)
        if (
            receipt.owner_id,
            receipt.workflow_id,
            receipt.stage_id,
            receipt.run_id,
        ) != (owner_id, workflow_id, stage_id, run.id):
            raise DomainError(
                "workflow_graph_inconsistent", "Current child receipt differs", 409
            )
        reason = f"workflow_child_{run.status}_unverified"
        status = (
            root.status
            if root.status in {"pause_requested", "cancel_requested"}
            else "blocked"
        )
        if (root.status, root.blocked_reason) != (status, reason):
            root.revision += 1
            root.status, root.blocked_reason = status, reason
            session.flush()
        return reason
