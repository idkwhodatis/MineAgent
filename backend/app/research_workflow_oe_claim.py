"""Atomic terminal OE allocation from actual released RD evidence, not status alone."""

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.engine_result_release import source_key
from backend.app.factor_services import publication
from backend.app.models import DataUsageExposure, ResearchRun, utcnow
from backend.app.oe_structured_preflight import budget_floor, freeze_inputs
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.oe_workflow_input import retained_workflow_submission_data
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_persistence import persist_workflow_child
from backend.app.oe_workflow_source import (
    WorkflowSourceReference,
    require_workflow_source,
)
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.research_input_resolution import resolve_model_input_versions
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_services import TERMINAL, quota_limit
from backend.app.research_workflow_history import read_selected_inputs
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCommand,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_native_guard import native_guard_from_publication
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.workflow_run import WorkflowOEContext
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import StageExecutionBinding
from packages.research_workflow.model_inputs import ModelStageInputSnapshot
from packages.research_workflow.oe_stage import (
    OEStageDefinition,
    WorkflowOEClaim,
    WorkflowOEClaimReceipt,
)
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)


def fail(code="workflow_oe_claim_changed"):
    raise DomainError(
        code, "Workflow structured child admission is unavailable or changed", 409
    )


def claim_oe_stage(session, owner_id, command, settings, *, admission):
    command = WorkflowOEClaim.model_validate_json(canonical_bytes(command))
    require_structured_workflow_schema(session)
    require_publication_admission(session, settings, admission)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .filter_by(owner_id=owner_id, id=command.workflow_id)
        .with_for_update(nowait=True)
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    raw, sha = canonical_bytes(command).decode(), digest(command)
    prior = session.scalar(
        select(ResearchWorkflowCommand).filter_by(
            owner_id=owner_id,
            workflow_id=root.id,
            idempotency_key=command.idempotency_key,
        )
    )
    if prior is not None:
        if (prior.command_canonical, prior.command_sha256) != (raw, sha):
            fail("workflow_command_conflict")
        receipt = WorkflowOEClaimReceipt.model_validate_json(prior.result_canonical)
        if canonical_bytes(receipt).decode() != prior.result_canonical or (
            receipt.workflow_id,
            receipt.command_id,
            receipt.command_sha256,
        ) != (root.id, prior.id, sha):
            fail()
        from backend.app.oe_workflow_saved import verify_workflow_oe_submission

        with native_guard_from_publication(session, settings, admission=admission):
            child = verify_workflow_oe_submission(
                session, owner_id, receipt.submission_id, settings
            )
        if (child.run_id, child.stage_id, child.workflow_id) != (
            receipt.child_run_id,
            receipt.stage_id,
            root.id,
        ):
            fail()
        return receipt
    if (
        root.revision != command.expected_revision
        or root.current_input_revision_id != command.input_revision_id
    ):
        fail("workflow_revision_conflict")
    if root.status not in {"ready", "blocked"} or (
        root.status == "blocked"
        and root.blocked_reason
        not in {"workflow_structured_pending", "workflow_structured_release_required"}
    ):
        fail()
    plan = read_guidance_plan(session, root.plan_canonical)
    if type(plan) is not RDStructuredGuidancePlan or (
        plan.owner_id,
        plan.sha256,
        plan.mode,
    ) != (owner_id, root.plan_sha256, root.mode):
        fail()
    if root.budget_canonical != canonical_bytes(
        plan.budget_ceiling
    ).decode() or root.budget_sha256 != digest(plan.budget_ceiling):
        fail()
    stages = tuple(
        session.scalars(
            select(ResearchWorkflowStage)
            .filter_by(owner_id=owner_id, workflow_id=root.id)
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(18)
        )
    )
    schedule = guidance_stage_schedule(plan)
    if (
        not stages
        or len(stages) != len(schedule) - 1
        or stages[-1].id != root.current_stage_id
        or stages[-1].kind != "rd_factor_generation"
    ):
        fail()
    total = plan.structured_template.budget
    for ordinal, row in enumerate(stages, 1):
        if (row.owner_id, row.workflow_id, row.ordinal, row.kind) != (
            owner_id,
            root.id,
            ordinal,
            schedule[ordinal - 1][0],
        ):
            fail()
        allocated = BudgetVector.model_validate_json(row.allocation_canonical)
        if (
            canonical_bytes(allocated).decode() != row.allocation_canonical
            or digest(allocated) != row.allocation_sha256
        ):
            fail()
        total = total.add(allocated)
    if not plan.budget_ceiling.covers(total):
        fail("workflow_budget_exhausted")
    with native_guard_from_publication(session, settings, admission=admission):
        checkpoint, actual = verify_rd_checkpoint(
            session, owner_id, root.id, root.latest_checkpoint_id, settings
        )
        reference = WorkflowSourceReference(root.id, checkpoint.id)
        require_workflow_source(
            session,
            owner_id,
            actual.child_run_id,
            actual.snapshot_id,
            reference,
            settings,
        )
        release = session.scalar(
            select(DataUsageExposure).filter_by(
                owner_id=owner_id,
                source_key=source_key(
                    "execution_snapshot", actual.snapshot_id, "factor"
                ),
            )
        )
        if release is None:
            fail("workflow_structured_release_required")
        selection = read_selected_inputs(session, owner_id, root.id)
        if selection.revision_id != command.input_revision_id:
            fail("workflow_revision_conflict")
        resolved = resolve_model_input_versions(
            session, owner_id, selection.refs, settings
        )
        definition = OEStageDefinition(
            plan_sha256=plan.sha256,
            structured_template_sha256=digest(plan.structured_template),
            predecessor_checkpoint_id=checkpoint.id,
            predecessor_checkpoint_sha256=checkpoint.terminal_sha256,
            parent_source_stage_id=actual.stage_id,
            source_run_id=actual.child_run_id,
            source_attempt_id=actual.attempt_id,
            source_generation=actual.generation,
            factor_version_id=actual.factor_version_id,
            snapshot_id=actual.snapshot_id,
            snapshot_manifest_sha256=actual.snapshot_manifest_sha256,
            analysis_id=actual.analysis_id,
            analysis_result_sha256=actual.analysis_result_sha256,
            release_exposure_id=release.id,
            release_provenance_sha256=digest(release.provenance),
        )
        stage_id, child_id = uuid4(), uuid4()
        inputs = ModelStageInputSnapshot(
            binding=StageExecutionBinding(
                owner_id=owner_id,
                workflow_id=root.id,
                stage_id=stage_id,
                input_revision_id=selection.revision_id,
                input_sha256=selection.sha256,
                definition_sha256=definition.sha256,
            ),
            selection=selection,
            inputs=resolved,
        )
        context = WorkflowOEContext(
            definition_sha256=definition.sha256,
            child_run_id=child_id,
            inputs=inputs,
            rd_stage_id=actual.stage_id,
            rd_checkpoint_id=checkpoint.id,
            rd_checkpoint_sha256=checkpoint.terminal_sha256,
            source_run_id=actual.child_run_id,
            source_attempt_id=actual.attempt_id,
            source_generation=actual.generation,
            snapshot_id=actual.snapshot_id,
            snapshot_manifest_sha256=actual.snapshot_manifest_sha256,
            factor_version_id=actual.factor_version_id,
            analysis_id=actual.analysis_id,
            analysis_result_sha256=actual.analysis_result_sha256,
            release_exposure_id=release.id,
            release_provenance_sha256=digest(release.provenance),
        )
        template = plan.structured_template
        request = WorkflowStructuredSearchRequest.model_validate_json(
            canonical_bytes(
                {
                    "snapshot_id": str(actual.snapshot_id),
                    "seed_parameters": template.seed_parameters.model_dump(mode="json"),
                    "objective": template.objective.model_dump(mode="json"),
                    "budget": template.budget.model_dump(mode="json"),
                    "maximum_mutation_slots": template.maximum_mutation_slots,
                    "model_profile_id": template.model_binding.profile_id,
                    "model_binding_sha256": template.model_binding.sha256,
                    "workflow_context": context.model_dump(mode="json"),
                }
            )
        )
        runtime = resolve_runtime(settings)
        _, binding = resolve_operator_profile(
            settings, owner_id, request.model_profile_id
        )
        if (
            binding != template.model_binding
            or runtime.runtime != template.runtime
            or not template.budget.covers(budget_floor(request, binding))
        ):
            fail("workflow_structured_binding_changed")
        active = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(
                ResearchRun.owner_id == owner_id, ResearchRun.status.not_in(TERMINAL)
            )
        )
        if active >= quota_limit(session, owner_id, settings):
            raise DomainError("run_quota", "Active run limit reached", 429)
        with retained_workflow_submission_data(
            session, owner_id, request, settings
        ) as data:
            fixed = freeze_inputs(data)
            checked = SimpleNamespace(
                preflight_sha256=digest(
                    {
                        "schema_version": "mineagent.workflow-oe-preflight.v1",
                        "request_sha256": request.sha256,
                        "fixed_input_sha256": digest(fixed),
                        "runtime_profile_sha256": digest(runtime),
                        "model_binding_sha256": binding.sha256,
                    }
                )
            )
            stage = ResearchWorkflowStage(
                id=stage_id,
                owner_id=owner_id,
                workflow_id=root.id,
                ordinal=len(schedule),
                input_revision_id=selection.revision_id,
                kind="structured_factor_execution",
                child_run_id=child_id,
                definition_canonical=canonical_bytes(definition).decode(),
                definition_sha256=definition.sha256,
                allocation_canonical=canonical_bytes(template.budget).decode(),
                allocation_sha256=digest(template.budget),
            )
            session.add(stage)
            root.current_stage_id, root.status, root.blocked_reason = (
                stage.id,
                "running",
                None,
            )
            root.revision += 1
            root.updated_at = utcnow()
            session.flush()
            child = persist_workflow_child(
                session,
                owner_id,
                f"workflow-oe:{stage.id}",
                request,
                checked,
                fixed,
                runtime,
                binding,
                data,
                settings=settings,
                admission=admission,
            )
            row = OEWorkflowSubmission(
                id=uuid4(),
                owner_id=owner_id,
                workflow_id=root.id,
                stage_id=stage.id,
                input_revision_id=selection.revision_id,
                run_id=child_id,
                submission_id=child.id,
                rd_stage_id=actual.stage_id,
                rd_checkpoint_id=checkpoint.id,
                source_run_id=actual.child_run_id,
                source_attempt_id=actual.attempt_id,
                source_generation=actual.generation,
                snapshot_id=actual.snapshot_id,
                factor_version_id=actual.factor_version_id,
                analysis_id=actual.analysis_id,
                release_exposure_id=release.id,
                request_sha256=request.sha256,
                config_sha256=session.get(ResearchRun, child_id).request_sha256,
                preflight_sha256=checked.preflight_sha256,
                runtime_profile_sha256=digest(runtime),
                definition_sha256=definition.sha256,
                selection_sha256=selection.sha256,
                resolved_inputs_sha256=inputs.sha256,
                rd_checkpoint_sha256=checkpoint.terminal_sha256,
                fixed_inputs_sha256=digest(fixed),
                submission_origin="workflow_stage",
            )
            session.add(row)
    command_id = uuid4()
    receipt = WorkflowOEClaimReceipt(
        workflow_id=root.id,
        command_id=command_id,
        command_sha256=sha,
        stage_id=stage.id,
        child_run_id=child_id,
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


def dispatch_oe_stage(factory, settings, *, owner_id, command):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        with publication(
            session, owner_id, settings, peak_bytes=0, peak_files=0
        ) as admission:
            receipt = claim_oe_stage(
                session, owner_id, command, settings, admission=admission
            )
        return receipt
