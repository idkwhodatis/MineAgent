"""Read the actual immutable workflow/OE attachment; no current launch grant."""

from sqlalchemy import select

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import ResearchRun
from backend.app.oe_structured_models import OEStructuredSpec, OEStructuredSubmission
from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
from backend.app.oe_workflow_input import retained_workflow_submission_data
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.research_workflow_input_reads import verify_workflow_inputs
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.oe_stage import OEStageDefinition
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)


def fail():
    raise DomainError(
        "workflow_oe_binding_changed",
        "Exact retained workflow child binding differs",
        409,
    )


def _one(session, model, **keys):
    rows = list(session.scalars(select(model).filter_by(**keys).limit(2)))
    if len(rows) != 1:
        fail()
    return rows[0]


def verify_workflow_oe_submission(session, owner_id, submission_id, settings):
    require_structured_workflow_schema(session)
    require_native_storage_guard(settings.storage_root)
    row = _one(
        session, OEWorkflowSubmission, owner_id=owner_id, submission_id=submission_id
    )
    root = _one(session, ResearchWorkflow, owner_id=owner_id, id=row.workflow_id)
    stage = _one(
        session,
        ResearchWorkflowStage,
        owner_id=owner_id,
        workflow_id=root.id,
        id=row.stage_id,
    )
    revision = _one(
        session,
        ResearchWorkflowRevision,
        owner_id=owner_id,
        workflow_id=root.id,
        id=row.input_revision_id,
    )
    run = _one(session, ResearchRun, owner_id=owner_id, id=row.run_id)
    submission = _one(
        session,
        OEStructuredSubmission,
        owner_id=owner_id,
        run_id=run.id,
        id=row.submission_id,
    )
    spec = _one(
        session,
        OEStructuredSpec,
        owner_id=owner_id,
        run_id=run.id,
        submission_id=submission.id,
    )
    try:
        plan = read_guidance_plan(session, root.plan_canonical)
        config = WorkflowStructuredRunConfig.model_validate_json(
            canonical_bytes(run.config)
        )
        request = WorkflowStructuredSearchRequest.model_validate_json(
            submission.request_canonical
        )
        selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
        definition = OEStageDefinition.model_validate_json(stage.definition_canonical)
        context = config.workflow_context
        binding = context.inputs.binding
        if type(plan) is not RDStructuredGuidancePlan:
            fail()
        template = plan.structured_template
        if (
            plan.owner_id != owner_id
            or plan.sha256 != root.plan_sha256
            or root.budget_canonical != canonical_bytes(plan.budget_ceiling).decode()
            or root.budget_sha256 != digest(plan.budget_ceiling)
            or stage.ordinal != len(guidance_stage_schedule(plan))
            or stage.kind != "structured_factor_execution"
            or stage.child_run_id != run.id
            or stage.input_revision_id != revision.id
            or canonical_bytes(selection).decode() != revision.inputs_canonical
            or selection.sha256 != revision.inputs_sha256
            or canonical_bytes(definition).decode() != stage.definition_canonical
            or definition.sha256 != stage.definition_sha256
            or definition.plan_sha256 != plan.sha256
            or definition.structured_template_sha256 != digest(template)
            or stage.allocation_canonical != canonical_bytes(template.budget).decode()
            or stage.allocation_sha256 != digest(template.budget)
            or config.budget != template.budget
            or config.model_binding != template.model_binding
            or config.runtime != template.runtime
            or request.objective != template.objective
            or request.maximum_mutation_slots != template.maximum_mutation_slots
            or request.seed_parameters.model_dump()
            != template.seed_parameters.model_dump()
            or request.workflow_context != context
            or config.workflow_request_sha256 != request.sha256
            or (
                binding.owner_id,
                binding.workflow_id,
                binding.stage_id,
                binding.input_revision_id,
                binding.input_sha256,
                binding.definition_sha256,
            )
            != (
                owner_id,
                root.id,
                stage.id,
                revision.id,
                selection.sha256,
                definition.sha256,
            )
            or context.inputs.selection != selection
            or canonical_bytes(request).decode() != submission.request_canonical
            or request.sha256 != submission.request_sha256
            or submission.origin != "workflow_stage"
            or row.submission_origin != "workflow_stage"
            or (run.purpose, run.engine, run.strategy_version_id, run.generation)
            != ("strategy_search", "openevolve", None, 1)
            or run.request_sha256 != digest(config)
            or (run.id, run.owner_id, run.dataset_version_id)
            != (
                config.search.run_id,
                config.search.owner_id,
                config.fixed_inputs.dataset_version_id,
            )
            or spec.request != request.model_dump(mode="json")
            or spec.request_canonical != submission.request_canonical
            or spec.config != run.config
            or spec.config_canonical != canonical_bytes(config).decode()
            or spec.config_sha256 != digest(config)
            or submission.config_sha256 != digest(config)
        ):
            fail()
        exact = {
            "request_sha256": request.sha256,
            "config_sha256": digest(config),
            "preflight_sha256": submission.preflight_sha256,
            "runtime_profile_sha256": digest(
                OEStructuredServerProfile(runtime=config.runtime)
            ),
            "definition_sha256": definition.sha256,
            "selection_sha256": selection.sha256,
            "resolved_inputs_sha256": context.inputs.sha256,
            "rd_checkpoint_sha256": context.rd_checkpoint_sha256,
            "fixed_inputs_sha256": digest(config.fixed_inputs),
            "rd_stage_id": context.rd_stage_id,
            "rd_checkpoint_id": context.rd_checkpoint_id,
            "source_run_id": context.source_run_id,
            "source_attempt_id": context.source_attempt_id,
            "source_generation": context.source_generation,
            "snapshot_id": context.snapshot_id,
            "factor_version_id": context.factor_version_id,
            "analysis_id": context.analysis_id,
            "release_exposure_id": context.release_exposure_id,
        }
        if any(getattr(row, key) != value for key, value in exact.items()):
            fail()
        if (
            definition.predecessor_checkpoint_id,
            definition.predecessor_checkpoint_sha256,
            definition.parent_source_stage_id,
            definition.source_run_id,
            definition.source_attempt_id,
            definition.source_generation,
            definition.snapshot_id,
            definition.snapshot_manifest_sha256,
            definition.factor_version_id,
            definition.analysis_id,
            definition.analysis_result_sha256,
            definition.release_exposure_id,
            definition.release_provenance_sha256,
        ) != (
            context.rd_checkpoint_id,
            context.rd_checkpoint_sha256,
            context.rd_stage_id,
            context.source_run_id,
            context.source_attempt_id,
            context.source_generation,
            context.snapshot_id,
            context.snapshot_manifest_sha256,
            context.factor_version_id,
            context.analysis_id,
            context.analysis_result_sha256,
            context.release_exposure_id,
            context.release_provenance_sha256,
        ):
            fail()
        expected_preflight = digest(
            {
                "schema_version": "mineagent.workflow-oe-preflight.v1",
                "request_sha256": request.sha256,
                "fixed_input_sha256": digest(config.fixed_inputs),
                "runtime_profile_sha256": exact["runtime_profile_sha256"],
                "model_binding_sha256": config.model_binding.sha256,
            }
        )
        if (
            expected_preflight != submission.preflight_sha256
            or spec.preflight_sha256 != expected_preflight
        ):
            fail()
        resolved = verify_workflow_inputs(session, owner_id, selection.refs, settings)
        if resolved != context.inputs.inputs:
            fail()
        with retained_workflow_submission_data(
            session, owner_id, request, settings
        ) as data:
            from backend.app.oe_structured_preflight import freeze_inputs

            if freeze_inputs(data) != config.fixed_inputs:
                fail()
        require_native_storage_guard(settings.storage_root)
        return row
    except (ValueError, TypeError, AttributeError, KeyError):
        fail()
