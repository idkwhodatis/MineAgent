"""Flush-only finite workflow creation and future-stage input CAS.

These services remain behind the uninstalled workflow capability. A plan is not
provider execution authority. Normalization gets a genuine local stage; model
child admission remains blocked until its exact submission adapter is installed.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import utcnow
from backend.app.research_input_dispatch import PUBLICATION_PEAK_BYTES
from backend.app.research_input_models import ResearchInputObject
from backend.app.research_input_storage import read_input_object
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import _replay, require_schema
from backend.app.research_workflow_input_reads import (
    resolve_workflow_inputs as resolve_model_input_versions,
)
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCommand,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.admission import (
    SelectWorkflowInputs,
    WorkflowCreated,
)
from packages.research_workflow.contracts import (
    WorkflowCommandReceipt,
    WorkflowInputSnapshot,
)


def fail(code="workflow_admission_changed"):
    raise DomainError(code, "Workflow admission is unavailable or changed", 409)


def _snapshot(owner, workflow, revision, refs):
    return WorkflowInputSnapshot(
        owner_id=owner, workflow_id=workflow, revision_id=revision, refs=refs
    )


def create_workflow(session, owner_id, request, settings):
    from backend.app.research_workflow_plan_codec import parse_workflow_creation
    from backend.app.research_workflow_structured_schemas import (
        CreateStructuredWorkflow,
    )

    request = parse_workflow_creation(canonical_bytes(request))
    if type(request) is CreateStructuredWorkflow:
        from backend.app.research_workflow_structured_public import (
            require_structured_workflow_schema,
        )

        require_structured_workflow_schema(session)
    require_schema(session)
    if request.plan.owner_id != owner_id or request.plan.normalize_external_first != (
        request.external_definition is not None
    ):
        fail()
    raw = canonical_bytes(request).decode()
    if len(raw.encode()) > 1100000:
        fail()
    _try_neutral_lock(session, f"submit:{owner_id}")
    prior = session.scalar(
        select(ResearchWorkflow).where(
            ResearchWorkflow.owner_id == owner_id,
            ResearchWorkflow.creation_key == request.idempotency_key,
        )
    )
    if prior is not None:
        if (prior.owner_id, prior.creation_canonical, prior.creation_sha256) != (
            owner_id,
            raw,
            digest(request),
        ):
            fail("workflow_command_conflict")
        result = WorkflowCreated.model_validate_json(prior.creation_result_canonical)
        if (
            canonical_bytes(result).decode() != prior.creation_result_canonical
            or result.workflow_id != prior.id
            or result.request_sha256 != digest(request)
        ):
            fail()
        initial = session.scalar(
            select(ResearchWorkflowRevision).where(
                ResearchWorkflowRevision.owner_id == owner_id,
                ResearchWorkflowRevision.workflow_id == prior.id,
                ResearchWorkflowRevision.id == result.input_revision_id,
            )
        )
        expected = _snapshot(owner_id, prior.id, result.input_revision_id, request.refs)
        if initial is None or (
            initial.owner_id,
            initial.workflow_id,
            initial.id,
            initial.ordinal,
            initial.inputs_canonical,
            initial.inputs_sha256,
            prior.plan_canonical,
            prior.plan_sha256,
        ) != (
            owner_id,
            prior.id,
            result.input_revision_id,
            1,
            canonical_bytes(expected).decode(),
            expected.sha256,
            canonical_bytes(request.plan).decode(),
            request.plan.sha256,
        ):
            fail()
        if request.external_definition is not None:
            first = session.scalar(
                select(ResearchWorkflowStage).where(
                    ResearchWorkflowStage.owner_id == owner_id,
                    ResearchWorkflowStage.workflow_id == prior.id,
                    ResearchWorkflowStage.id == result.current_stage_id,
                )
            )
            if first is None or (
                first.owner_id,
                first.workflow_id,
                first.ordinal,
                first.input_revision_id,
                first.kind,
                first.child_run_id,
                first.definition_canonical,
                first.definition_sha256,
            ) != (
                owner_id,
                prior.id,
                1,
                result.input_revision_id,
                "external_normalization",
                None,
                canonical_bytes(request.external_definition).decode(),
                digest(request.external_definition),
            ):
                fail()
        elif result.current_stage_id is not None:
            fail()
        return result
    workflow_id, revision_id = uuid4(), uuid4()
    selection = _snapshot(owner_id, workflow_id, revision_id, request.refs)
    resolve_model_input_versions(session, owner_id, selection.refs, settings)
    stage = None
    if request.external_definition is not None:
        definition = request.external_definition
        obj = session.scalar(
            select(ResearchInputObject).where(
                ResearchInputObject.owner_id == owner_id,
                ResearchInputObject.id == definition.raw_object_id,
            )
        )
        if (
            obj is None
            or obj.kind != "raw_external"
            or obj.sha256 != definition.raw_sha256
        ):
            fail()
        read_input_object(settings.storage_root, obj, owner_id)
        # Raw intake is already retained/counted in physical baseline. The
        # incremental bound is aggregate canonical outputs plus one atomic temp.
        allocation = BudgetVector(storage_bytes=PUBLICATION_PEAK_BYTES)
        child_total = BudgetVector(
            **{
                key: getattr(request.plan.research_template.budget, key)
                * request.plan.maximum_iterations
                for key in BudgetVector.model_fields
            }
        )
        child_total = child_total.add(
            BudgetVector(storage_bytes=65536 * (request.plan.maximum_iterations - 1))
        )
        if type(request) is CreateStructuredWorkflow:
            child_total = child_total.add(request.plan.structured_template.budget)
        if not request.plan.budget_ceiling.covers(child_total.add(allocation)):
            fail("workflow_budget_exhausted")
        stage = ResearchWorkflowStage(
            id=uuid4(),
            owner_id=owner_id,
            workflow_id=workflow_id,
            ordinal=1,
            input_revision_id=revision_id,
            kind="external_normalization",
            child_run_id=None,
            definition_canonical=canonical_bytes(definition).decode(),
            definition_sha256=digest(definition),
            allocation_canonical=canonical_bytes(allocation).decode(),
            allocation_sha256=digest(allocation),
        )
    status = "ready" if stage is not None else "blocked"
    result = WorkflowCreated(
        workflow_id=workflow_id,
        input_revision_id=revision_id,
        request_sha256=digest(request),
        status=status,
        current_stage_id=None if stage is None else stage.id,
    )
    root = ResearchWorkflow(
        id=workflow_id,
        owner_id=owner_id,
        revision=1,
        creation_key=request.idempotency_key,
        creation_canonical=raw,
        creation_sha256=digest(request),
        creation_result_canonical=canonical_bytes(result).decode(),
        plan_canonical=canonical_bytes(request.plan).decode(),
        plan_sha256=request.plan.sha256,
        budget_canonical=canonical_bytes(request.plan.budget_ceiling).decode(),
        budget_sha256=digest(request.plan.budget_ceiling),
        mode=request.plan.mode,
        status=status,
        blocked_reason=None
        if stage is not None
        else "workflow_child_adapter_unavailable",
        current_input_revision_id=revision_id,
        current_stage_id=None if stage is None else stage.id,
        latest_checkpoint_id=None,
    )
    revision = ResearchWorkflowRevision(
        id=revision_id,
        owner_id=owner_id,
        workflow_id=workflow_id,
        ordinal=1,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    session.add(root)
    session.add(revision)
    if stage is not None:
        session.add(stage)
    session.flush()
    return result


def select_workflow_inputs(session, owner_id, request, settings):
    request = SelectWorkflowInputs.model_validate_json(canonical_bytes(request))
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .where(
            ResearchWorkflow.owner_id == owner_id,
            ResearchWorkflow.id == request.workflow_id,
        )
        .with_for_update(nowait=True)
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    prior = session.scalar(
        select(ResearchWorkflowCommand).where(
            ResearchWorkflowCommand.owner_id == owner_id,
            ResearchWorkflowCommand.workflow_id == root.id,
            ResearchWorkflowCommand.idempotency_key == request.idempotency_key,
        )
    )
    if prior is not None:
        return _replay(prior, owner_id, request)
    if root.owner_id != owner_id or root.revision != request.expected_revision:
        fail("workflow_revision_conflict")
    if root.revision >= 1000000 or root.status in {
        "completed",
        "cancelled",
        "cancel_requested",
    }:
        fail()
    selection = _snapshot(owner_id, root.id, uuid4(), request.refs)
    resolve_model_input_versions(session, owner_id, selection.refs, settings)
    # Running/current stage and its input FK are deliberately untouched.
    root.revision += 1
    root.current_input_revision_id = selection.revision_id
    root.updated_at = utcnow()
    revision = ResearchWorkflowRevision(
        id=selection.revision_id,
        owner_id=owner_id,
        workflow_id=root.id,
        ordinal=root.revision,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    result = WorkflowCommandReceipt(
        workflow_id=root.id,
        command_id=uuid4(),
        command_sha256=digest(request),
        revision=root.revision,
        status=root.status,
        checkpoint_id=root.latest_checkpoint_id,
        current_stage_id=root.current_stage_id,
    )
    session.add(revision)
    session.add(
        ResearchWorkflowCommand(
            id=result.command_id,
            owner_id=owner_id,
            workflow_id=root.id,
            idempotency_key=request.idempotency_key,
            command_canonical=canonical_bytes(request).decode(),
            command_sha256=digest(request),
            result_canonical=canonical_bytes(result).decode(),
        )
    )
    session.flush()
    return result
