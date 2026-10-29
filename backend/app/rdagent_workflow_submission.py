"""028 atomic new-child writer. No caller snapshot, run ID, pins or live fence.

The orchestrator allocates the actual stage and its child UUID under root CAS in
this SAME transaction. This service never attaches an old research run. Activation
requires the explicit workflow schema and its exact graph triggers.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import OutboxEvent
from backend.app.rdagent_provider_declaration import (
    prepare_provider_declaration_under_native_guard as prepare_provider_declaration,
)
from backend.app.rdagent_seed_services import (
    resolve_seed_parent_under_native_guard as resolve_seed_parent,
)
from backend.app.rdagent_submission_services import (
    _insert_declared_queued_graph,
    resolve_research_declaration,
)
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.research_workflow_native_guard import native_guard_from_publication
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
from packages.research_workflow.contracts import StageExecutionBinding
from packages.research_workflow.model_inputs import ModelStageInputSnapshot


def fail():
    raise DomainError(
        "workflow_child_changed", "Workflow child declaration changed", 409
    )


def submit_workflow_child(
    session, owner_id, workflow_id, stage_id, settings, *, admission
):
    """Flush only; caller commits allocated stage, new run and outbox together.

    Current-stage admission is not used by subsequent queue claim or historical
    reads. Those validate the immutable receipt and explicit run controls instead.
    """
    with native_guard_from_publication(session, settings, admission=admission):
        return _submit_workflow_child_under_native_guard(
            session, owner_id, workflow_id, stage_id, settings, admission=admission
        )


def _submit_workflow_child_under_native_guard(
    session, owner_id, workflow_id, stage_id, settings, *, admission
):
    from backend.app.research_input_resolution import verify_model_input_versions
    from backend.app.research_workflow_rd_stage import require_current_rd_stage

    checked = require_current_rd_stage(
        session, owner_id, workflow_id, stage_id, settings, admission=admission
    )
    root, stage, plan = checked.root, checked.stage, checked.plan
    selection, definition = checked.selection, checked.definition
    # Current-stage validator authorizes NEW allocation only. Orchestrator command
    # replay returns its actual prior result rather than calling this writer twice.
    if (
        session.scalar(
            select(FactorWorkflowSubmission).filter_by(
                owner_id=owner_id, workflow_id=workflow_id, stage_id=stage_id
            )
        )
        is not None
    ):
        fail()
    if stage.child_run_id is None:
        fail()
    predecessor = None
    if definition.research_iteration != 0:
        from backend.app.research_workflow_rd_predecessor import verify_rd_predecessor

        predecessor = verify_rd_predecessor(
            session, owner_id, workflow_id, stage_id, settings
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
    request = stage_research_request(
        _original_research_request(root, plan), plan, definition
    )
    from backend.app.rdagent_provider_declaration import settings_for_frozen_controller

    selected_settings = settings_for_frozen_controller(settings, plan.research_template)
    prepared = prepare_provider_declaration(
        session, owner_id, request, selected_settings
    )
    if (
        prepared.request != request
        or canonical_bytes(prepared.config) != canonical_bytes(plan.research_template)
        or request.objective != plan.objective
        or request.source_policy != plan.source_policy
        or prepared.runtime_profile.qlib_installed_parent_image_id
        != plan.qlib_installed_parent_image_id
        or {
            lock.role: lock.sha256 for lock in prepared.config.runtime.dependency_locks
        }["analysis"]
        != plan.analysis_dependency_lock_sha256
    ):
        fail()
    resolved = verify_model_input_versions(session, owner_id, selection.refs, settings)
    inputs = ModelStageInputSnapshot(
        binding=StageExecutionBinding(
            owner_id=owner_id,
            workflow_id=workflow_id,
            stage_id=stage_id,
            input_revision_id=selection.revision_id,
            input_sha256=selection.sha256,
            definition_sha256=definition.sha256,
        ),
        selection=selection,
        inputs=resolved,
    )
    parent_id = definition.parent_factor_version_id
    if (
        definition.research_iteration == 0
        and parent_id != plan.initial_parent_factor_version_id
    ):
        fail()
    seed = (
        None
        if parent_id is None
        else resolve_seed_parent(session, owner_id, parent_id, settings)
    )
    if prepared.preflight.parent != (None if seed is None else seed.reference):
        fail()
    if predecessor is not None and (
        seed is None
        or seed.reference.code_sha256 != predecessor.checkpoint.source_sha256
    ):
        fail()
    config = WorkflowFactorResearchConfig.model_validate(
        {
            **prepared.config.model_dump(mode="python"),
            "schema_version": "mineagent.factor-research.v3",
            "model_profile": "workflow.rdagent.v1",
            "workflow_prompt_layout": "generation_user.v1",
            "workflow_child_run_id": stage.child_run_id,
            "workflow_inputs": inputs,
            "workflow_inputs_sha256": inputs.sha256,
            "workflow_parent": None if seed is None else seed.reference,
        }
    )
    first_workflow_messages(config, seed)  # Full actual prompt bound, never truncate.
    # Reread retained input bytes before writing any run. A detached prepared DTO
    # is neither source authority nor permission to bypass source/label admission.
    fresh_config, source = resolve_research_declaration(
        session,
        owner_id,
        request,
        settings,
        prepared.runtime_profile.runtime,
        expected_input_sha256=prepared.config.input.sha256,
        model_binding=prepared.config.model_binding,
        steer_snapshot=prepared.config.steer_snapshot,
        controller_selection=plan.research_template.controller_profile,
    )
    if fresh_config != prepared.config:
        fail()
    run, spec, analysis = _insert_declared_queued_graph(
        session,
        owner_id,
        request,
        f"workflow-stage:{stage_id}",
        settings,
        prepared.runtime_profile.runtime,
        prepared.runtime_profile.qlib_installed_parent_image_id,
        config=config,
        source=source,
        run_id=stage.child_run_id,
    )
    receipt = FactorWorkflowSubmission(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        input_revision_id=selection.revision_id,
        run_id=run.id,
        spec_id=spec.id,
        plan_id=analysis.id,
        request_canonical=canonical_bytes(request).decode("ascii"),
        request_sha256=request.sha256,
        preflight_sha256=prepared.preflight.preflight_sha256,
        runtime_profile_sha256=digest(prepared.runtime_profile),
        config_sha256=config.sha256,
        selection_sha256=selection.sha256,
        resolved_inputs_sha256=inputs.sha256,
        definition_sha256=definition.sha256,
        submission_origin="workflow_stage",
    )
    session.add(receipt)
    session.add(OutboxEvent(id=uuid4(), run_id=run.id, generation=1))
    session.flush()
    return receipt


def stage_research_request(original, plan, definition):
    """Derive immutable request bytes, never authorize a predecessor or child.

    A later child keeps the original research declaration but explicitly seeds
    from its stage definition. The caller must independently verify the actual
    previous child, terminal checkpoint and released feedback chain before use.
    """
    from backend.app.rdagent_provider_submission_contracts import (
        RDProviderSeedRequest,
        parse_provider_request,
    )
    from packages.research_workflow.rd_stage import RDStageDefinition
    from packages.research_workflow.structured_plan import parse_guidance_plan

    try:
        original = parse_provider_request(original.model_dump(mode="python"))
        plan = parse_guidance_plan(canonical_bytes(plan))
        definition = RDStageDefinition.model_validate_json(canonical_bytes(definition))
        if (
            original.selected_steer_version_ids
            or definition.plan_sha256 != plan.sha256
            or definition.research_iteration >= plan.maximum_iterations
            or getattr(original, "parent_factor_version_id", None)
            != plan.initial_parent_factor_version_id
            or original.objective != plan.objective
            or original.source_policy != plan.source_policy
            or original.model_profile_id
            != plan.research_template.model_binding.profile_id
            or original.budget != plan.research_template.budget
        ):
            fail()
        if definition.research_iteration == 0:
            if (
                definition.parent_factor_version_id
                != plan.initial_parent_factor_version_id
            ):
                fail()
            return original
        return RDProviderSeedRequest.model_validate_json(
            canonical_bytes(
                {
                    **original.model_dump(mode="json"),
                    "schema_version": "mineagent.rdagent-provider-seeded-submission.v1",
                    "parent_factor_version_id": str(
                        definition.parent_factor_version_id
                    ),
                }
            )
        )
    except (ValueError, TypeError, AttributeError):
        fail()


def _original_research_request(root, plan):
    from backend.app.research_workflow_plan_codec import parse_workflow_creation
    from backend.app.research_workflow_public_schemas import WorkflowSubmit
    from backend.app.research_workflow_structured_schemas import (
        CreateStructuredWorkflow,
        StructuredWorkflowSubmit,
    )

    try:
        creation = parse_workflow_creation(root.creation_canonical.encode("ascii"))
        if (
            canonical_bytes(creation).decode("ascii") != root.creation_canonical
            or digest(creation) != root.creation_sha256
            or creation.plan != plan
            or creation.public_submission_canonical is None
        ):
            fail()
        body_type = (
            StructuredWorkflowSubmit
            if type(creation) is CreateStructuredWorkflow
            else WorkflowSubmit
        )
        body = body_type.model_validate_json(creation.public_submission_canonical)
        if (
            canonical_bytes(body).decode("ascii")
            != creation.public_submission_canonical
            or body.idempotency_key != root.creation_key
            or body.request.research.selected_steer_version_ids
        ):
            fail()
        return body.request.research
    except (ValueError, TypeError, AttributeError):
        fail()
