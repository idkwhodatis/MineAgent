"""Strictly decreasing immutable predecessor chain, never current-head authority."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.feedback_checkpoint import FeedbackCheckpoint
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)


@dataclass(frozen=True)
class VerifiedRDPredecessor:
    definition: RDStageDefinition
    checkpoint: RDStageCheckpoint
    parent_stage_id: UUID
    checkpoint_id: UUID
    feedback_checkpoint: FeedbackCheckpoint
    feedback_checkpoint_id: UUID


def fail():
    raise DomainError(
        "workflow_predecessor_changed",
        "Exact immutable workflow predecessor is unavailable",
        409,
    )


def _decode(row, model):
    if row is None:
        fail()
    value = model.model_validate_json(row.terminal_canonical)
    if (
        canonical_bytes(value).decode() != row.terminal_canonical
        or digest(value) != row.terminal_sha256
    ):
        fail()
    return value


def verify_rd_predecessor_metadata(session, owner_id, workflow_id, stage_id):
    """Relational delivery metadata ONLY; no physical read or launch grant."""
    require_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).filter_by(owner_id=owner_id, id=workflow_id)
    )
    stages = list(
        session.scalars(
            select(ResearchWorkflowStage)
            .filter_by(owner_id=owner_id, workflow_id=workflow_id)
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(18)
        )
    )
    if (
        root is None
        or len(stages) > 17
        or (root.owner_id, root.id) != (owner_id, workflow_id)
    ):
        fail()
    byid = {row.id: row for row in stages}
    stage = byid.get(stage_id)
    if stage is None or stage.kind != "rd_factor_generation":
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    definition = RDStageDefinition.model_validate_json(stage.definition_canonical)
    schedule = stage_schedule(plan)
    if (
        plan.owner_id != owner_id
        or plan.sha256 != root.plan_sha256
        or canonical_bytes(plan).decode() != root.plan_canonical
        or canonical_bytes(definition).decode() != stage.definition_canonical
        or definition.sha256 != stage.definition_sha256
        or definition.plan_sha256 != plan.sha256
        or not 0 < definition.research_iteration < plan.maximum_iterations
        or not 3 <= stage.ordinal <= len(schedule)
        or schedule[stage.ordinal - 1]
        != ("rd_factor_generation", definition.research_iteration)
    ):
        fail()
    for ordinal, row in enumerate(stages, 1):
        if (
            (row.owner_id, row.workflow_id, row.ordinal)
            != (owner_id, workflow_id, ordinal)
            or ordinal > len(schedule)
            or row.kind != schedule[ordinal - 1][0]
        ):
            fail()
    parent = byid.get(definition.parent_source_stage_id)
    feedback = next((row for row in stages if row.ordinal == stage.ordinal - 1), None)
    if (
        parent is None
        or feedback is None
        or parent.kind != "rd_factor_generation"
        or feedback.kind != "experiment_feedback_projection"
        or parent.ordinal != stage.ordinal - 2
    ):
        fail()
    prior_definition = RDStageDefinition.model_validate_json(
        parent.definition_canonical
    )
    if (
        prior_definition.plan_sha256 != plan.sha256
        or canonical_bytes(prior_definition).decode() != parent.definition_canonical
        or prior_definition.sha256 != parent.definition_sha256
        or prior_definition.research_iteration != definition.research_iteration - 1
    ):
        fail()
    feedback_row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=workflow_id,
            id=definition.predecessor_checkpoint_id,
            stage_id=feedback.id,
        )
    )
    projection = _decode(feedback_row, FeedbackCheckpoint)
    if (
        feedback_row.owner_id,
        feedback_row.workflow_id,
        feedback_row.stage_id,
        projection.owner_id,
        projection.workflow_id,
        projection.stage_id,
        projection.definition_sha256,
        projection.allocation_sha256,
        projection.input_revision_id,
        projection.state,
    ) != (
        owner_id,
        workflow_id,
        feedback.id,
        owner_id,
        workflow_id,
        feedback.id,
        feedback.definition_sha256,
        feedback.allocation_sha256,
        feedback.input_revision_id,
        "succeeded",
    ):
        fail()
    from packages.research_inputs.feedback import FeedbackProjectionDefinition

    projection_definition = FeedbackProjectionDefinition.model_validate_json(
        feedback.definition_canonical
    )
    if (
        canonical_bytes(projection_definition).decode() != feedback.definition_canonical
        or digest(projection_definition) != feedback.definition_sha256
        or projection_definition.source != projection.source
    ):
        fail()
    revision = session.scalar(
        select(ResearchWorkflowRevision).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=feedback.input_revision_id
        )
    )
    if revision is None:
        fail()
    selected = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
    if (
        canonical_bytes(selected).decode() != revision.inputs_canonical
        or selected.sha256 != revision.inputs_sha256
        or selected.sha256 != projection.input_sha256
        or (selected.owner_id, selected.workflow_id, selected.revision_id)
        != (owner_id, workflow_id, feedback.input_revision_id)
    ):
        fail()
    rd_row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=workflow_id,
            id=projection.rd_checkpoint_id,
            stage_id=parent.id,
        )
    )
    checkpoint = _decode(rd_row, RDStageCheckpoint)
    if (
        rd_row.owner_id,
        rd_row.workflow_id,
        rd_row.stage_id,
        checkpoint.owner_id,
        checkpoint.workflow_id,
        checkpoint.stage_id,
        checkpoint.child_run_id,
        checkpoint.factor_version_id,
        checkpoint.definition_sha256,
    ) != (
        owner_id,
        workflow_id,
        parent.id,
        owner_id,
        workflow_id,
        parent.id,
        parent.child_run_id,
        definition.parent_factor_version_id,
        parent.definition_sha256,
    ):
        fail()
    source = projection.source
    if (
        source.workflow_id,
        source.stage_id,
        source.child_run_id,
        source.output_kind,
        source.output_id,
        source.content_sha256,
    ) != (
        workflow_id,
        parent.id,
        checkpoint.child_run_id,
        "direct_analysis",
        checkpoint.analysis_id,
        checkpoint.analysis_result_sha256,
    ):
        fail()
    return VerifiedRDPredecessor(
        definition, checkpoint, parent.id, rd_row.id, projection, feedback_row.id
    )


def verify_rd_predecessor(session, owner_id, workflow_id, stage_id, settings):
    """Existing native guard, actual stopped prior child + released feedback bytes."""
    from backend.app.research_input_feedback import (
        validate_feedback_terminal_under_native_guard,
    )

    require_native_storage_guard(settings.storage_root)
    value = verify_rd_predecessor_metadata(session, owner_id, workflow_id, stage_id)
    _, actual = verify_rd_checkpoint(
        session, owner_id, workflow_id, value.checkpoint_id, settings
    )
    if actual != value.checkpoint:
        fail()
    stored = value.feedback_checkpoint
    facts = validate_feedback_terminal_under_native_guard(
        session, owner_id, workflow_id, stored.stage_id, settings
    )
    if (
        facts.derivation_id,
        facts.terminal_event_id,
        facts.terminal_event_sha256,
        facts.state,
        facts.outputs,
        facts.exposure_ids,
    ) != (
        stored.derivation_id,
        stored.terminal_event_id,
        stored.terminal_event_sha256,
        "succeeded",
        stored.outputs,
        stored.exposure_ids,
    ) or tuple((o.id, o.kind, o.sha256, o.size_bytes) for o in facts.objects) != tuple(
        (o.id, o.kind, o.sha256, o.size_bytes) for o in stored.objects
    ):
        fail()
    require_native_storage_guard(settings.storage_root)
    if (
        verify_rd_predecessor_metadata(session, owner_id, workflow_id, stage_id)
        != value
    ):
        fail()
    return value
