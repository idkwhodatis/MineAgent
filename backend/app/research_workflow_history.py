"""Owned retained stage history, not a checkpoint or result disclosure grant.

Only the actual deterministic external derivation is installed here. Engine
adapters must verify the new child submission binding, not attach an old owned
run. No live state is inferred from timestamps or from an absent receipt.
"""

from sqlalchemy import select

from backend.app.research_input_derivations import verify_derivation
from backend.app.research_input_history import read_derivation_history
from backend.app.research_input_models import ResearchInputDerivation
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_input_reads import (
    verify_workflow_inputs as verify_model_input_versions,
)
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_workflow.contracts import (
    WorkflowInputSnapshot,
    WorkflowStageHistory,
)


def _changed():
    raise DomainError("workflow_history_changed", "Retained stage graph differs", 409)


def read_stage_history(session, owner_id, workflow_id, stage_id, settings):
    """Read immutable history without locking current selection or committing.

    Later selection edits/disable do not change the accepted stage. Output text
    is privately verified through the historical resolver and never returned.
    Settlement is not inferred from a deterministic success receipt.
    """
    require_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).where(
            ResearchWorkflow.owner_id == owner_id, ResearchWorkflow.id == workflow_id
        )
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    stage = session.scalar(
        select(ResearchWorkflowStage).where(
            ResearchWorkflowStage.owner_id == owner_id,
            ResearchWorkflowStage.workflow_id == workflow_id,
            ResearchWorkflowStage.id == stage_id,
        )
    )
    if stage is None:
        raise DomainError("not_found", "Stage not found", 404)
    if (root.owner_id, root.id, stage.owner_id, stage.workflow_id, stage.id) != (
        owner_id,
        workflow_id,
        owner_id,
        workflow_id,
        stage_id,
    ):
        _changed()
    if stage.kind == "structured_factor_execution":
        from backend.app.research_workflow_oe_history import read_oe_stage_history

        return read_oe_stage_history(session, owner_id, root, stage, settings)
    if stage.kind == "rd_factor_generation":
        from backend.app.research_workflow_rd_history import read_rd_stage_history

        return read_rd_stage_history(session, owner_id, root, stage, settings)
    if stage.kind not in {"external_normalization", "experiment_feedback_projection"}:
        raise DomainError(
            "workflow_history_adapter_unavailable", "Stage adapter is unavailable", 409
        )
    revision = session.scalar(
        select(ResearchWorkflowRevision).where(
            ResearchWorkflowRevision.owner_id == owner_id,
            ResearchWorkflowRevision.workflow_id == workflow_id,
            ResearchWorkflowRevision.id == stage.input_revision_id,
        )
    )
    if revision is None or stage.child_run_id is not None:
        _changed()
    try:
        if (
            len(revision.inputs_canonical.encode()) > 32768
            or len(stage.definition_canonical.encode()) > 8192
        ):
            _changed()
        selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
        from packages.research_inputs.feedback import FeedbackProjectionDefinition

        definition_type = (
            FeedbackProjectionDefinition
            if stage.kind == "experiment_feedback_projection"
            else ExternalNormalizationDefinition
        )
        definition = definition_type.model_validate_json(stage.definition_canonical)
        if (
            (revision.owner_id, revision.workflow_id, revision.id)
            != (owner_id, workflow_id, stage.input_revision_id)
            or (selection.owner_id, selection.workflow_id, selection.revision_id)
            != (owner_id, workflow_id, stage.input_revision_id)
            or canonical_bytes(selection).decode() != revision.inputs_canonical
            or selection.sha256 != revision.inputs_sha256
            or canonical_bytes(definition).decode() != stage.definition_canonical
            or digest(definition) != stage.definition_sha256
        ):
            _changed()
    except (ValueError, TypeError):
        _changed()
    if selection.refs:
        verify_model_input_versions(session, owner_id, selection.refs, settings)
    derivation = session.scalar(
        select(ResearchInputDerivation).where(
            ResearchInputDerivation.owner_id == owner_id,
            ResearchInputDerivation.workflow_id == workflow_id,
            ResearchInputDerivation.stage_id == stage_id,
        )
    )
    history = None
    if derivation is not None:
        if stage.kind == "experiment_feedback_projection":
            from backend.app.research_input_feedback import verify_feedback_derivation

            verify_feedback_derivation(derivation, owner_id, stage, definition)
        else:
            verify_derivation(derivation, owner_id, stage, definition)
        history = read_derivation_history(session, owner_id, workflow_id, stage_id)
        if history.derivation_id != derivation.id:
            _changed()
        if history.outputs:
            verify_model_input_versions(session, owner_id, history.outputs, settings)
    checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint).where(
            ResearchWorkflowCheckpoint.owner_id == owner_id,
            ResearchWorkflowCheckpoint.workflow_id == workflow_id,
            ResearchWorkflowCheckpoint.stage_id == stage_id,
        )
    )
    if checkpoint is not None:
        from packages.research_workflow.local_checkpoint import (
            LocalNormalizationCheckpoint,
        )

        try:
            if len(checkpoint.terminal_canonical.encode()) > 65536 or history is None:
                _changed()
            from packages.research_workflow.feedback_checkpoint import (
                FeedbackCheckpoint,
            )

            checkpoint_type = (
                FeedbackCheckpoint
                if stage.kind == "experiment_feedback_projection"
                else LocalNormalizationCheckpoint
            )
            value = checkpoint_type.model_validate_json(checkpoint.terminal_canonical)
            if (
                checkpoint.owner_id,
                checkpoint.workflow_id,
                checkpoint.stage_id,
                value.owner_id,
                value.workflow_id,
                value.stage_id,
                value.input_revision_id,
                value.input_sha256,
                value.definition_sha256,
                value.allocation_sha256,
                value.derivation_id,
                value.terminal_event_id,
                value.terminal_event_sha256,
                value.state,
                value.outputs,
            ) != (
                owner_id,
                workflow_id,
                stage_id,
                owner_id,
                workflow_id,
                stage_id,
                revision.id,
                revision.inputs_sha256,
                stage.definition_sha256,
                stage.allocation_sha256,
                history.derivation_id,
                history.terminal_event_id,
                history.terminal_event_sha256,
                history.status,
                history.outputs,
            ):
                _changed()
            if stage.kind == "external_normalization":
                if (value.objects[0].id, value.objects[0].sha256) != (
                    definition.raw_object_id,
                    definition.raw_sha256,
                ):
                    _changed()
            else:
                from backend.app.research_input_feedback import (
                    validate_feedback_terminal_under_native_guard,
                )
                from backend.app.research_workflow_input_reads import (
                    retained_input_guard,
                )

                with retained_input_guard(settings):
                    actual = validate_feedback_terminal_under_native_guard(
                        session, owner_id, workflow_id, stage_id, settings
                    )
                if (
                    value.source != definition.source
                    or (
                        actual.derivation_id,
                        actual.terminal_event_id,
                        actual.terminal_event_sha256,
                        actual.state,
                        actual.outputs,
                        actual.exposure_ids,
                    )
                    != (
                        value.derivation_id,
                        value.terminal_event_id,
                        value.terminal_event_sha256,
                        value.state,
                        value.outputs,
                        value.exposure_ids,
                    )
                    or tuple(
                        (o.id, o.kind, o.sha256, o.size_bytes) for o in actual.objects
                    )
                    != tuple(
                        (o.id, o.kind, o.sha256, o.size_bytes) for o in value.objects
                    )
                ):
                    _changed()
            if (
                canonical_bytes(value).decode() != checkpoint.terminal_canonical
                or digest(value) != checkpoint.terminal_sha256
            ):
                _changed()
        except (ValueError, TypeError):
            _changed()
    return WorkflowStageHistory(
        stage_id=stage.id,
        ordinal=stage.ordinal,
        kind=stage.kind,
        input_revision_id=revision.id,
        input_sha256=revision.inputs_sha256,
        definition_sha256=stage.definition_sha256,
        child_run_id=None,
        consumption="not_submitted_to_model",
        checkpoint_id=None if checkpoint is None else checkpoint.id,
        source_status="not_recorded" if history is None else history.status,
        child_attempt_id=None,
        started_at=None if history is None else history.started_at,
        finished_at=None if history is None else history.terminal_at,
        safe_error_code=None if history is None else history.error_code,
        input_refs=selection.refs,
        derived_input_refs=() if history is None else history.outputs,
        output_refs=(),
        model_requests=(),
        containment="not_applicable",
        settlement="not_observed",
    )


def read_workflow(session, owner_id, workflow_id, settings):
    """Current control metadata plus genuine current-stage projection.

    Capability and child adapter failures remain explicit. UI controls are only
    request affordances; command execution rechecks CAS/owner/current records.
    """
    from packages.research_workflow.contracts import (
        WorkflowControls,
        WorkflowStageView,
        WorkflowView,
    )

    require_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).where(
            ResearchWorkflow.owner_id == owner_id, ResearchWorkflow.id == workflow_id
        )
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    try:
        if len(root.plan_canonical.encode()) > 1048576:
            _changed()
        plan = read_guidance_plan(session, root.plan_canonical)
        if (
            root.owner_id,
            root.id,
            plan.owner_id,
            plan.sha256,
            canonical_bytes(plan).decode(),
        ) != (owner_id, workflow_id, owner_id, root.plan_sha256, root.plan_canonical):
            _changed()
    except (ValueError, TypeError):
        _changed()
    current = None
    if root.current_stage_id is not None:
        history = read_stage_history(
            session, owner_id, workflow_id, root.current_stage_id, settings
        )
        from packages.research_workflow.oe_history import WorkflowOEStageView

        stage_view = (
            WorkflowOEStageView
            if history.kind == "structured_factor_execution"
            else WorkflowStageView
        )
        current = stage_view(
            **{name: getattr(history, name) for name in WorkflowStageView.model_fields}
        )
    if root.mode != plan.mode:
        _changed()
    terminal = root.status in {"completed", "cancelled"}
    from packages.research_workflow.oe_history import StructuredWorkflowView
    from packages.research_workflow.structured_plan import RDStructuredGuidancePlan

    view_type = (
        StructuredWorkflowView
        if type(plan) is RDStructuredGuidancePlan
        else WorkflowView
    )
    return view_type(
        workflow_id=root.id,
        revision=root.revision,
        mode=root.mode,
        status=root.status,
        selected_input_revision_id=root.current_input_revision_id,
        current_stage=current,
        latest_checkpoint_id=root.latest_checkpoint_id,
        blocked_reason=(
            None
            if root.blocked_reason is None
            else root.blocked_reason
            if root.blocked_reason
            in {
                "workflow_child_adapter_unavailable",
                "waiting_release",
                "workflow_feedback_release_required",
                "workflow_waiting_containment",
                "workflow_feedback_pending",
                "workflow_structured_pending",
                "workflow_structured_release_required",
                "workflow_next_child_pending",
                "workflow_checkpoint_adapter_unavailable",
                "workflow_budget_exhausted",
                "workflow_stage_failed",
            }
            else "workflow_blocked"
        ),
        controls=WorkflowControls(
            pause_after_stage=not terminal
            and root.status not in {"paused", "pause_requested", "cancel_requested"},
            cancel=not terminal and root.status != "cancel_requested",
            resume=root.status == "paused"
            and (
                root.current_stage_id is None or root.latest_checkpoint_id is not None
            ),
            reason_code="physical_checkpoint_rechecked_on_resume"
            if root.current_stage_id is not None
            else "owner_revision_rechecked_on_command",
        ),
    )


def read_selected_inputs(session, owner_id, workflow_id):
    """Current future selection metadata, not frozen stage consumption.

    Read immutable revision by the observed root pointer. Later edits/disable
    do not erase a saved selection; new stage acceptance checks eligibility.
    """
    require_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).where(
            ResearchWorkflow.owner_id == owner_id, ResearchWorkflow.id == workflow_id
        )
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    revision = session.scalar(
        select(ResearchWorkflowRevision).where(
            ResearchWorkflowRevision.owner_id == owner_id,
            ResearchWorkflowRevision.workflow_id == workflow_id,
            ResearchWorkflowRevision.id == root.current_input_revision_id,
        )
    )
    if revision is None or len(revision.inputs_canonical.encode()) > 32768:
        _changed()
    try:
        value = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
        if (
            (
                root.owner_id,
                root.id,
                revision.owner_id,
                revision.workflow_id,
                revision.id,
            )
            != (
                owner_id,
                workflow_id,
                owner_id,
                workflow_id,
                root.current_input_revision_id,
            )
            or (value.owner_id, value.workflow_id, value.revision_id)
            != (owner_id, workflow_id, revision.id)
            or value.sha256 != revision.inputs_sha256
            or canonical_bytes(value).decode() != revision.inputs_canonical
        ):
            _changed()
    except (ValueError, TypeError):
        _changed()
    return value
