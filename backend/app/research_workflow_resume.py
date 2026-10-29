"""Explicit resume revalidates a real safe boundary; no old unit is extended."""

from sqlalchemy import select

from backend.app.research_input_storage import require_publication_admission
from backend.app.research_workflow_models import (
    ResearchWorkflowCheckpoint,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest


def fail():
    raise DomainError(
        "workflow_checkpoint_changed", "An actual safe checkpoint is required", 409
    )


def verify_resume_checkpoint(session, owner_id, root, settings, *, admission):
    require_publication_admission(session, settings, admission)
    stage = session.scalar(
        select(ResearchWorkflowStage).filter_by(
            owner_id=owner_id, workflow_id=root.id, id=root.current_stage_id
        )
    )
    row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=root.id,
            stage_id=root.current_stage_id,
            id=root.latest_checkpoint_id,
        )
    )
    if (
        stage is None
        or row is None
        or (
            stage.owner_id,
            stage.workflow_id,
            row.owner_id,
            row.workflow_id,
            row.stage_id,
        )
        != (owner_id, root.id, owner_id, root.id, stage.id)
    ):
        fail()
    if stage.kind == "external_normalization":
        from backend.app.research_workflow_local_checkpoint import (
            observe_external_terminal,
        )

        identity, facts = observe_external_terminal(
            session,
            owner_id,
            root.id,
            stage.id,
            root.revision,
            settings,
            admission=admission,
        )
        if identity != row.id or facts.state != "succeeded":
            fail()
        return "workflow_child_adapter_unavailable"
    from backend.app.research_workflow_native_guard import native_guard_from_publication

    with native_guard_from_publication(session, settings, admission=admission):
        if stage.kind == "structured_factor_execution":
            from backend.app.research_workflow_oe_checkpoint import verify_oe_checkpoint
            from packages.research_workflow.structured_plan import (
                RDStructuredGuidancePlan,
                guidance_stage_schedule,
            )

            _, facts = verify_oe_checkpoint(
                session, owner_id, root.id, row.id, settings
            )
            plan = read_guidance_plan(session, root.plan_canonical)
            if (
                type(plan) is not RDStructuredGuidancePlan
                or stage.ordinal != len(guidance_stage_schedule(plan))
                or facts.stage_id != stage.id
            ):
                fail()
            return "workflow_finite_plan_complete"
        if stage.kind == "rd_factor_generation":
            from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint
            from packages.research_workflow.rd_stage import RDStageDefinition

            _, facts = verify_rd_checkpoint(
                session, owner_id, root.id, row.id, settings
            )
            plan = read_guidance_plan(session, root.plan_canonical)
            definition = RDStageDefinition.model_validate_json(
                stage.definition_canonical
            )
            if (
                plan.owner_id != owner_id
                or plan.sha256 != root.plan_sha256
                or canonical_bytes(plan).decode() != root.plan_canonical
                or canonical_bytes(definition).decode() != stage.definition_canonical
                or definition.sha256 != stage.definition_sha256
                or definition.plan_sha256 != plan.sha256
                or definition.research_iteration + 1 > plan.maximum_iterations
            ):
                fail()
            if definition.research_iteration + 1 == plan.maximum_iterations:
                from packages.research_workflow.structured_plan import (
                    RDStructuredGuidancePlan,
                )

                if type(plan) is not RDStructuredGuidancePlan:
                    fail()
                return "workflow_structured_pending"
            return "workflow_feedback_pending"
        if stage.kind == "experiment_feedback_projection":
            from backend.app.research_input_feedback import (
                validate_feedback_terminal_under_native_guard,
            )
            from backend.app.research_workflow_feedback import _binding
            from packages.research_workflow.feedback_checkpoint import (
                FeedbackCheckpoint,
            )

            facts = validate_feedback_terminal_under_native_guard(
                session, owner_id, root.id, stage.id, settings
            )
            bound_root, bound_stage, _, definition, prior, _ = _binding(
                session, owner_id, root.id, stage.id
            )
            value = FeedbackCheckpoint.model_validate_json(row.terminal_canonical)
            if (
                bound_root.id != root.id
                or bound_stage.id != stage.id
                or value.rd_checkpoint_id != prior.id
                or value.source != definition.source
                or value.input_revision_id != stage.input_revision_id
                or canonical_bytes(value).decode() != row.terminal_canonical
                or digest(value) != row.terminal_sha256
                or (
                    value.owner_id,
                    value.workflow_id,
                    value.stage_id,
                    value.definition_sha256,
                    value.allocation_sha256,
                )
                != (
                    owner_id,
                    root.id,
                    stage.id,
                    stage.definition_sha256,
                    stage.allocation_sha256,
                )
                or (
                    facts.state,
                    facts.derivation_id,
                    facts.terminal_event_id,
                    facts.terminal_event_sha256,
                    facts.outputs,
                    facts.exposure_ids,
                )
                != (
                    "succeeded",
                    value.derivation_id,
                    value.terminal_event_id,
                    value.terminal_event_sha256,
                    value.outputs,
                    value.exposure_ids,
                )
                or tuple((o.id, o.kind, o.sha256, o.size_bytes) for o in facts.objects)
                != tuple((o.id, o.kind, o.sha256, o.size_bytes) for o in value.objects)
            ):
                fail()
            return "workflow_next_child_pending"
    fail()


def resume_workflow(factory, settings, *, owner_id, command):
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_services import publication
    from backend.app.research_workflow_control import request_control
    from packages.research_workflow.contracts import WorkflowCommand

    command = WorkflowCommand.model_validate_json(canonical_bytes(command))
    if command.action != "resume":
        fail()
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            result = request_control(
                session, owner_id, command, settings=settings, admission=admission
            )
        return result  # Existing publisher performs the only bounded commit.
