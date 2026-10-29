"""Close only the genuine deterministic normalization stage under existing quota.

No physical process was launched by this adapter. It does not create a stop
receipt, model cost settlement or a child run, and never auto-selects outputs.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.models import utcnow
from backend.app.research_input_dispatch import PUBLICATION_PEAK_BYTES
from backend.app.research_input_terminal import validate_external_terminal
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowModelConsumption,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.normalization import MAX_OUTPUT_BYTES
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.local_checkpoint import (
    LocalCheckpointObject,
    LocalNormalizationCheckpoint,
)


def fail():
    raise DomainError(
        "workflow_local_boundary_changed",
        "Local stage boundary is unavailable or changed",
        409,
    )


def observe_external_terminal(
    session, owner_id, workflow_id, stage_id, expected_revision, settings, *, admission
):
    """Flush exact checkpoint and root transition; caller commits after quota exit."""
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .where(
            ResearchWorkflow.owner_id == owner_id, ResearchWorkflow.id == workflow_id
        )
        .with_for_update(nowait=True)
    )
    stage = session.scalar(
        select(ResearchWorkflowStage).where(
            ResearchWorkflowStage.owner_id == owner_id,
            ResearchWorkflowStage.workflow_id == workflow_id,
            ResearchWorkflowStage.id == stage_id,
        )
    )
    if (
        root is None
        or stage is None
        or (
            root.owner_id,
            root.id,
            stage.owner_id,
            stage.workflow_id,
            stage.id,
            stage.kind,
            stage.child_run_id,
        )
        != (
            owner_id,
            workflow_id,
            owner_id,
            workflow_id,
            stage_id,
            "external_normalization",
            None,
        )
    ):
        fail()
    prior = session.scalar(
        select(ResearchWorkflowCheckpoint).where(
            ResearchWorkflowCheckpoint.owner_id == owner_id,
            ResearchWorkflowCheckpoint.workflow_id == workflow_id,
            ResearchWorkflowCheckpoint.stage_id == stage_id,
        )
    )
    if prior is None and (
        type(expected_revision) is not int
        or root.revision != expected_revision
        or root.revision >= 1000000
        or root.current_stage_id != stage_id
        or root.status
        not in {"ready", "running", "pause_requested", "cancel_requested"}
    ):
        fail()
    if (
        session.scalar(
            select(ResearchWorkflowModelConsumption.id)
            .where(ResearchWorkflowModelConsumption.stage_id == stage_id)
            .limit(1)
        )
        is not None
    ):
        fail()
    revision = session.scalar(
        select(ResearchWorkflowRevision).where(
            ResearchWorkflowRevision.owner_id == owner_id,
            ResearchWorkflowRevision.workflow_id == workflow_id,
            ResearchWorkflowRevision.id == stage.input_revision_id,
        )
    )
    if revision is None:
        fail()
    if (
        len(root.plan_canonical.encode()) > 1048576
        or len(revision.inputs_canonical.encode()) > 32768
    ):
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
    allocation = BudgetVector(storage_bytes=PUBLICATION_PEAK_BYTES)
    child_total = BudgetVector(
        **{
            key: getattr(plan.research_template.budget, key) * plan.maximum_iterations
            for key in BudgetVector.model_fields
        }
    )
    if not plan.budget_ceiling.covers(child_total.add(allocation)):
        fail()
    if (
        not plan.normalize_external_first
        or stage.ordinal != 1
        or (revision.owner_id, revision.workflow_id, revision.id)
        != (owner_id, workflow_id, stage.input_revision_id)
        or plan.owner_id != owner_id
        or plan.sha256 != root.plan_sha256
        or canonical_bytes(plan).decode() != root.plan_canonical
        or root.mode != plan.mode
        or (selection.owner_id, selection.workflow_id, selection.revision_id)
        != (owner_id, workflow_id, stage.input_revision_id)
        or selection.sha256 != revision.inputs_sha256
        or canonical_bytes(selection).decode() != revision.inputs_canonical
        or stage.allocation_canonical != canonical_bytes(allocation).decode()
        or stage.allocation_sha256 != digest(allocation)
    ):
        fail()
    facts = validate_external_terminal(
        session, owner_id, workflow_id, stage_id, settings, admission=admission
    )
    if (
        sum(
            obj.size_bytes for obj in facts.objects if obj.kind == "normalized_external"
        )
        > MAX_OUTPUT_BYTES
    ):
        fail()
    payload = LocalNormalizationCheckpoint(
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        input_revision_id=stage.input_revision_id,
        input_sha256=selection.sha256,
        definition_sha256=stage.definition_sha256,
        allocation_sha256=stage.allocation_sha256,
        derivation_id=facts.derivation_id,
        terminal_event_id=facts.terminal_event_id,
        terminal_event_sha256=facts.terminal_event_sha256,
        state=facts.state,
        outputs=facts.outputs,
        objects=tuple(
            LocalCheckpointObject(
                id=obj.id, kind=obj.kind, sha256=obj.sha256, size_bytes=obj.size_bytes
            )
            for obj in facts.objects
        ),
    )
    if prior is not None:
        if (
            prior.owner_id,
            prior.workflow_id,
            prior.stage_id,
            prior.terminal_canonical,
            prior.terminal_sha256,
        ) != (
            owner_id,
            workflow_id,
            stage_id,
            canonical_bytes(payload).decode(),
            digest(payload),
        ):
            fail()
        from backend.app.research_workflow_control import (
            acknowledge_verified_checkpoint_control,
        )

        if acknowledge_verified_checkpoint_control(
            root, stage_id, prior.id, expected_revision
        ):
            session.flush()
        return prior.id, payload
    row = ResearchWorkflowCheckpoint(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        terminal_canonical=canonical_bytes(payload).decode(),
        terminal_sha256=digest(payload),
    )
    root.latest_checkpoint_id = row.id
    root.revision += 1
    root.updated_at = utcnow()
    if root.status == "cancel_requested":
        root.status, root.blocked_reason = "cancelled", None
    elif facts.state != "succeeded":
        root.status, root.blocked_reason = "blocked", "workflow_stage_failed"
    elif root.status == "pause_requested" or root.mode == "pause_after_stage":
        root.status, root.blocked_reason = "paused", None
    else:
        # The next actual RD adapter must re-read this graph and admit a NEW run.
        root.status, root.blocked_reason = (
            "blocked",
            "workflow_child_adapter_unavailable",
        )
    session.add(row)
    session.flush()
    return row.id, payload


def checkpoint_external_stage(
    factory, settings, *, owner_id, workflow_id, stage_id, expected_revision
):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            result = observe_external_terminal(
                session,
                owner_id,
                workflow_id,
                stage_id,
                expected_revision,
                settings,
                admission=admission,
            )
        # publication() commits the transaction before its successful exit.
        return result
