"""Immutable workflow child replay/worker binding, not fresh-stage admission.

No current root head/status, steer active flag or today's provider registry is
used to reinterpret an accepted child. Explicit run cancellation is separate.
This reader verifies retained source/input bytes but grants no attempt or launch.
"""

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_provider_submission_contracts import provider_commitment
from backend.app.rdagent_submission_services import resolve_research_declaration
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_submission import (
    _original_research_request,
    fail,
    stage_research_request,
)
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.rd_stage import RDStageDefinition


def _owned(session, model, owner_id, identity):
    row = session.scalar(select(model).filter_by(owner_id=owner_id, id=identity))
    if row is None or row.owner_id != owner_id or row.id != identity:
        fail()
    return row


def verify_workflow_submission(session, owner_id, submission_id, settings):
    from backend.app.factor_native_io import native_storage_guard

    with native_storage_guard(settings.storage_root):
        return verify_workflow_submission_under_native_guard(
            session, owner_id, submission_id, settings
        )


def verify_workflow_submission_under_native_guard(
    session, owner_id, submission_id, settings
):
    """Historical full proof with an existing native guard, never a nested flock."""
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.rdagent_seed_services import resolve_seed_parent_under_native_guard

    require_native_storage_guard(settings.storage_root)
    row = _verify_workflow_submission(
        session,
        owner_id,
        submission_id,
        settings,
        read_inputs=True,
        seed_reader=resolve_seed_parent_under_native_guard,
    )
    require_native_storage_guard(settings.storage_root)
    return row


def verify_workflow_submission_metadata(session, owner_id, submission_id):
    """Delivery graph only; no retained-file or execution/launch proof.

    Dispatcher has no storage lifecycle and only creates a durable queue job.
    Actual claim MUST call the full reader before allocating a real attempt.
    """
    return _verify_workflow_submission(
        session, owner_id, submission_id, None, read_inputs=False, seed_reader=None
    )


def _verify_workflow_submission(
    session, owner_id, submission_id, settings, *, read_inputs, seed_reader
):
    from backend.app.research_input_resolution import verify_model_input_versions
    from backend.app.research_workflow_control import require_schema

    require_schema(session)
    row = _owned(session, FactorWorkflowSubmission, owner_id, submission_id)
    root = _owned(session, ResearchWorkflow, owner_id, row.workflow_id)
    stage = _owned(session, ResearchWorkflowStage, owner_id, row.stage_id)
    revision = _owned(
        session, ResearchWorkflowRevision, owner_id, row.input_revision_id
    )
    run = _owned(session, ResearchRun, owner_id, row.run_id)
    spec = _owned(session, FactorResearchSpec, owner_id, row.spec_id)
    analysis = _owned(session, FactorAnalysisPlan, owner_id, row.plan_id)
    try:
        config = WorkflowFactorResearchConfig.model_validate_json(
            canonical_bytes(run.config)
        )
        plan = read_guidance_plan(session, root.plan_canonical)
        selection = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
        definition = RDStageDefinition.model_validate_json(stage.definition_canonical)
        request = stage_research_request(
            _original_research_request(root, plan), plan, definition
        )
        binding = config.workflow_inputs.binding
        if (
            row.submission_origin != "workflow_stage"
            or (stage.workflow_id, revision.workflow_id) != (root.id, root.id)
            or stage.input_revision_id != revision.id
            or stage.child_run_id != run.id
            or (spec.run_id, analysis.run_id, analysis.spec_id)
            != (run.id, run.id, spec.id)
            or config.workflow_child_run_id != run.id
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
            or config.workflow_inputs.selection != selection
            or selection.owner_id != owner_id
            or selection.workflow_id != root.id
            or selection.revision_id != revision.id
            or canonical_bytes(selection).decode("ascii") != revision.inputs_canonical
            or selection.sha256 != revision.inputs_sha256
            or row.selection_sha256 != selection.sha256
            or row.resolved_inputs_sha256 != config.workflow_inputs.sha256
            or row.definition_sha256 != definition.sha256
            or stage.definition_sha256 != definition.sha256
            or canonical_bytes(definition).decode("ascii") != stage.definition_canonical
            or definition.plan_sha256 != plan.sha256
            or plan.owner_id != owner_id
            or plan.sha256 != root.plan_sha256
            or canonical_bytes(plan).decode("ascii") != root.plan_canonical
            or root.budget_canonical
            != canonical_bytes(plan.budget_ceiling).decode("ascii")
            or root.budget_sha256 != digest(plan.budget_ceiling)
            or request.objective != plan.objective
            or request.source_policy != plan.source_policy
            or getattr(request, "parent_factor_version_id", None)
            != definition.parent_factor_version_id
            or request.model_profile_id != config.model_binding.profile_id
            or stage.kind != "rd_factor_generation"
            or stage.allocation_canonical
            != canonical_bytes(config.budget).decode("ascii")
            or stage.allocation_sha256 != digest(config.budget)
            or run.purpose != "factor_research"
            or run.engine != "rdagent"
            or run.idempotency_key != f"workflow-stage:{stage.id}"
            or run.config != config.model_dump(mode="json")
            or spec.config != run.config
            or (
                row.config_sha256,
                spec.config_sha256,
                run.request_sha256,
                analysis.config_sha256,
            )
            != (config.sha256,) * 4
            or (spec.input_sha256, analysis.input_sha256) != (config.input.sha256,) * 2
            or canonical_bytes(request).decode("ascii") != row.request_canonical
            or request.sha256 != row.request_sha256
            or analysis.objective_canonical
            != canonical_bytes(request.objective).decode("ascii")
            or analysis.objective != request.objective.model_dump(mode="json")
            or analysis.objective_sha256 != request.objective.sha256
            or analysis.source_policy != request.source_policy.model_dump(mode="json")
            or analysis.source_policy_sha256 != digest(request.source_policy)
            or analysis.label_source_dataset_id != request.dataset_version_id
            or run.dataset_version_id != request.dataset_version_id
            or analysis.label_source_manifest_sha256
            != config.input.dataset.content_sha256
            or analysis.qlib_image_id != config.runtime.analysis_image
            or analysis.qlib_installed_parent_image_id
            != plan.qlib_installed_parent_image_id
            or analysis.analysis_dependency_lock_sha256
            != plan.analysis_dependency_lock_sha256
        ):
            fail()
        predecessor = None
        if definition.research_iteration == 0:
            if (
                definition.parent_source_stage_id is not None
                or definition.parent_factor_version_id
                != plan.initial_parent_factor_version_id
            ):
                fail()
        else:
            from backend.app.research_workflow_rd_predecessor import (
                verify_rd_predecessor,
                verify_rd_predecessor_metadata,
            )

            predecessor = (
                verify_rd_predecessor(session, owner_id, root.id, stage.id, settings)
                if read_inputs
                else verify_rd_predecessor_metadata(
                    session, owner_id, root.id, stage.id
                )
            )
            if (
                predecessor.definition != definition
                or predecessor.parent_stage_id != definition.parent_source_stage_id
                or predecessor.feedback_checkpoint_id
                != definition.predecessor_checkpoint_id
                or predecessor.checkpoint.factor_version_id
                != definition.parent_factor_version_id
            ):
                fail()
        parent = config.workflow_parent
        if predecessor is not None and (
            parent is None or parent.code_sha256 != predecessor.checkpoint.source_sha256
        ):
            fail()
        if (
            None if parent is None else parent.factor_version_id
        ) != definition.parent_factor_version_id:
            fail()
        if read_inputs:
            actual_inputs = verify_model_input_versions(
                session, owner_id, selection.refs, settings
            )
            if tuple(actual_inputs) != config.workflow_inputs.inputs:
                fail()
            seed = (
                None
                if parent is None
                else seed_reader(session, owner_id, parent.factor_version_id, settings)
            )
            if predecessor is not None and (
                seed is None
                or seed.reference.code_sha256 != predecessor.checkpoint.source_sha256
            ):
                fail()
            first_workflow_messages(config, seed)
        from packages.rdagent_adapter.controller_profile import (
            controller_selection_fields,
        )

        runtime = RDProviderServerRuntimeProfile(
            runtime=config.runtime,
            **controller_selection_fields(config),
            qlib_installed_parent_image_id=analysis.qlib_installed_parent_image_id,
        )
        if (
            digest(runtime) != row.runtime_profile_sha256
            or provider_commitment(
                owner_id,
                request,
                dataset_sha256=config.input.dataset.content_sha256,
                input_sha256=config.input.sha256,
                profile_sha256=digest(runtime),
                model_binding_sha256=config.model_binding.sha256,
                steer_snapshot_sha256=config.steer_snapshot.sha256,
                parent_binding_sha256=None if parent is None else parent.sha256,
            )
            != row.preflight_sha256
        ):
            fail()
        if read_inputs:
            declared, _source = resolve_research_declaration(
                session,
                owner_id,
                request,
                settings,
                config.runtime,
                expected_input_sha256=config.input.sha256,
                model_binding=config.model_binding,
                steer_snapshot=config.steer_snapshot,
                controller_selection=config.controller_profile,
            )
            if canonical_bytes(declared) != canonical_bytes(plan.research_template):
                fail()
        expected = {
            **plan.research_template.model_dump(mode="json"),
            "schema_version": "mineagent.factor-research.v3",
            "model_profile": "workflow.rdagent.v1",
            "workflow_child_run_id": str(run.id),
            "workflow_inputs": config.workflow_inputs.model_dump(mode="json"),
            "workflow_inputs_sha256": config.workflow_inputs_sha256,
            "workflow_parent": None
            if parent is None
            else parent.model_dump(mode="json"),
        }
        if config.workflow_prompt_layout is not None:
            expected["workflow_prompt_layout"] = config.workflow_prompt_layout
        if (
            WorkflowFactorResearchConfig.model_validate_json(canonical_bytes(expected))
            != config
        ):
            fail()
    except (ValueError, TypeError, AttributeError):
        fail()
    return row
