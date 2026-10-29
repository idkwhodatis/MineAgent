"""Bounded deterministic feedback allocation after an actual RD checkpoint.

Allocating a stage is not disclosing, computing, selecting or applying feedback.
The source runner separately performs the fixed release-qualified read.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import utcnow
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import SourceOutputRef
from packages.research_inputs.feedback import FeedbackProjectionDefinition
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)

FEEDBACK_PEAK_BYTES = 65536  # One canonical <=32KiB output plus one atomic temp.


def fail(code="workflow_feedback_stage_changed"):
    raise DomainError(
        code, "Feedback stage cannot be allocated from this boundary", 409
    )


def allocate_feedback_stage(
    session, owner_id, workflow_id, expected_revision, settings, *, admission
):
    require_schema(session)
    require_publication_admission(session, settings, admission)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .filter_by(owner_id=owner_id, id=workflow_id)
        .with_for_update(nowait=True)
    )
    if root is None or (root.owner_id, root.id) != (owner_id, workflow_id):
        fail()
    stages = list(
        session.scalars(
            select(ResearchWorkflowStage)
            .filter_by(owner_id=owner_id, workflow_id=workflow_id)
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(18)
        )
    )
    if not stages or len(stages) > 17 or root.current_stage_id != stages[-1].id:
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    schedule = stage_schedule(plan)
    if (
        plan.owner_id != owner_id
        or plan.sha256 != root.plan_sha256
        or canonical_bytes(plan).decode() != root.plan_canonical
        or root.mode != plan.mode
    ):
        fail()
    for ordinal, stage in enumerate(stages, 1):
        if (
            (stage.owner_id, stage.workflow_id, stage.ordinal)
            != (owner_id, workflow_id, ordinal)
            or ordinal > len(schedule)
            or stage.kind != schedule[ordinal - 1][0]
        ):
            fail()
    if stages[-1].kind == "experiment_feedback_projection":
        from backend.app.research_workflow_feedback import _binding

        _binding(session, owner_id, workflow_id, stages[-1].id)
        return stages[-1]  # Original committed allocation only, not a new claim.
    if (
        type(expected_revision) is not int
        or root.revision != expected_revision
        or root.revision >= 1000000
        or root.status not in {"ready", "blocked"}
        or root.blocked_reason not in {None, "workflow_feedback_pending"}
    ):
        fail()
    previous = stages[-1]
    if (
        previous.kind != "rd_factor_generation"
        or len(stages) >= len(schedule)
        or schedule[len(stages)][0] != "experiment_feedback_projection"
    ):
        fail()
    definition = RDStageDefinition.model_validate_json(previous.definition_canonical)
    if (
        canonical_bytes(definition).decode() != previous.definition_canonical
        or definition.sha256 != previous.definition_sha256
        or definition.plan_sha256 != plan.sha256
        or schedule[previous.ordinal - 1][1] != definition.research_iteration
    ):
        fail()
    checkpoint = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id,
            workflow_id=workflow_id,
            id=root.latest_checkpoint_id,
            stage_id=previous.id,
        )
    )
    if checkpoint is None:
        fail()
    facts = RDStageCheckpoint.model_validate_json(checkpoint.terminal_canonical)
    if (
        canonical_bytes(facts).decode() != checkpoint.terminal_canonical
        or digest(facts) != checkpoint.terminal_sha256
        or (
            checkpoint.owner_id,
            checkpoint.workflow_id,
            checkpoint.stage_id,
            facts.owner_id,
            facts.workflow_id,
            facts.stage_id,
            facts.child_run_id,
            facts.definition_sha256,
        )
        != (
            owner_id,
            workflow_id,
            previous.id,
            owner_id,
            workflow_id,
            previous.id,
            previous.child_run_id,
            previous.definition_sha256,
        )
    ):
        fail()
    allocation = BudgetVector(storage_bytes=FEEDBACK_PEAK_BYTES)
    total = BudgetVector()
    for stage in stages:
        value = BudgetVector.model_validate_json(stage.allocation_canonical)
        if (
            canonical_bytes(value).decode() != stage.allocation_canonical
            or digest(value) != stage.allocation_sha256
        ):
            fail()
        total = total.add(value)
    # Reserve room for all still-declared children and feedback stages. This is
    # an immutable allocation ceiling, not a duplicate actual usage ledger.
    remaining_children = plan.maximum_iterations - definition.research_iteration - 1
    future = BudgetVector(
        **{
            key: getattr(plan.research_template.budget, key) * remaining_children
            for key in BudgetVector.model_fields
        }
    )
    future = future.add(
        BudgetVector(storage_bytes=FEEDBACK_PEAK_BYTES * remaining_children)
    )
    from packages.research_workflow.structured_plan import terminal_structured_budget

    future = future.add(terminal_structured_budget(plan))
    if not plan.budget_ceiling.covers(total.add(future)):
        fail("workflow_budget_exhausted")
    target = FeedbackProjectionDefinition(
        source=SourceOutputRef(
            workflow_id=workflow_id,
            stage_id=previous.id,
            child_run_id=facts.child_run_id,
            output_kind="direct_analysis",
            output_id=facts.analysis_id,
            content_sha256=facts.analysis_result_sha256,
        )
    )
    stage = ResearchWorkflowStage(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        ordinal=len(stages) + 1,
        input_revision_id=root.current_input_revision_id,
        kind="experiment_feedback_projection",
        child_run_id=None,
        definition_canonical=canonical_bytes(target).decode(),
        definition_sha256=digest(target),
        allocation_canonical=canonical_bytes(allocation).decode(),
        allocation_sha256=digest(allocation),
    )
    root.current_stage_id = stage.id
    root.status = "ready"
    root.blocked_reason = None
    root.revision += 1
    root.updated_at = utcnow()
    session.add(stage)
    session.flush()
    require_publication_admission(session, settings, admission)
    return stage


def dispatch_feedback_stage(
    factory, settings, *, owner_id, workflow_id, expected_revision
):
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_services import publication

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            row = allocate_feedback_stage(
                session,
                owner_id,
                workflow_id,
                expected_revision,
                settings,
                admission=admission,
            )
            stage_id = row.id
        return stage_id  # Existing quota context committed before ACK.
