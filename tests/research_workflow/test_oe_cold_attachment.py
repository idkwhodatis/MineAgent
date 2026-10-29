"""Strict retained contract joins; no fabricated physical completion authority."""

from uuid import uuid4

import pytest

from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.workflow_oe_backup import validate_submission_binding
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    TypedFrozenCandidateDomain,
)
from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_run import FixedExecutionInputs
from packages.openevolve_structured.workflow_run import (
    WorkflowOEContext,
    WorkflowStructuredRunConfig,
)
from packages.research_workflow.contracts import (
    StageExecutionBinding,
    WorkflowInputSnapshot,
)
from packages.research_workflow.model_inputs import ModelStageInputSnapshot
from packages.research_workflow.oe_stage import OEStageDefinition
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    guidance_stage_schedule,
)
from tests.openevolve.test_workflow_run import config as prior_config
from tests.research_workflow.test_structured_plan import combined


@pytest.fixture
def declaration():
    plan = RDStructuredGuidancePlan.model_validate_json(canonical_bytes(combined()))
    template = plan.structured_template
    owner, wid, sid, rid, runid = plan.owner_id, uuid4(), uuid4(), uuid4(), uuid4()
    fixed = FixedExecutionInputs.model_validate_json(
        canonical_bytes(prior_config()["fixed_inputs"])
    )
    definition = OEStageDefinition(
        plan_sha256=plan.sha256,
        structured_template_sha256=digest(template),
        predecessor_checkpoint_id=uuid4(),
        predecessor_checkpoint_sha256="b" * 64,
        parent_source_stage_id=uuid4(),
        source_run_id=uuid4(),
        source_attempt_id=uuid4(),
        source_generation=1,
        factor_version_id=fixed.factor_version_id,
        snapshot_id=fixed.snapshot_id,
        snapshot_manifest_sha256=fixed.snapshot_manifest_sha256,
        analysis_id=uuid4(),
        analysis_result_sha256="c" * 64,
        release_exposure_id=fixed.release_receipt_ids[0],
        release_provenance_sha256="d" * 64,
    )
    selection = WorkflowInputSnapshot(owner_id=owner, workflow_id=wid, revision_id=rid)
    binding = StageExecutionBinding(
        owner_id=owner,
        workflow_id=wid,
        stage_id=sid,
        input_revision_id=rid,
        input_sha256=selection.sha256,
        definition_sha256=definition.sha256,
    )
    context = WorkflowOEContext(
        definition_sha256=definition.sha256,
        child_run_id=runid,
        inputs=ModelStageInputSnapshot(binding=binding, selection=selection),
        rd_stage_id=definition.parent_source_stage_id,
        rd_checkpoint_id=definition.predecessor_checkpoint_id,
        rd_checkpoint_sha256=definition.predecessor_checkpoint_sha256,
        **{
            key: getattr(definition, key)
            for key in (
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "snapshot_id",
                "snapshot_manifest_sha256",
                "factor_version_id",
                "analysis_id",
                "analysis_result_sha256",
                "release_exposure_id",
                "release_provenance_sha256",
            )
        },
    )
    request = WorkflowStructuredSearchRequest(
        snapshot_id=fixed.snapshot_id,
        seed_parameters=template.seed_parameters.model_dump(),
        objective=template.objective,
        budget=template.budget,
        maximum_mutation_slots=template.maximum_mutation_slots,
        model_profile_id=template.model_binding.profile_id,
        model_binding_sha256=template.model_binding.sha256,
        workflow_context=context,
    )
    program = request.seed_parameters.program(
        plan.research_template.input.window.model_dump()
    )
    search = StructuredSearchSpec(
        owner_id=owner,
        run_id=runid,
        execution_binding_id=runid,
        execution_binding_sha256=digest(fixed),
        domain=TypedFrozenCandidateDomain(seed_program=program),
        seed_candidate_sha256=digest(CandidateDocument(program=program)),
        objective=template.objective,
        maximum_mutation_slots=template.maximum_mutation_slots,
    )
    config = WorkflowStructuredRunConfig(
        search=search,
        fixed_inputs=fixed,
        model_binding=template.model_binding,
        runtime=template.runtime,
        budget=template.budget,
        workflow_request_sha256=request.sha256,
        workflow_context=context,
    )
    scope = {"owner_id": str(owner), "workflow_id": str(wid)}
    stage = dict(
        id=str(sid),
        **scope,
        kind="structured_factor_execution",
        ordinal=len(guidance_stage_schedule(plan)),
        child_run_id=str(runid),
        input_revision_id=str(rid),
        allocation_canonical=canonical_bytes(template.budget).decode(),
        allocation_sha256=digest(template.budget),
    )
    revision = dict(id=str(rid), **scope)
    runtime_sha = digest(OEStructuredServerProfile(runtime=config.runtime))
    preflight = digest(
        {
            "schema_version": "mineagent.workflow-oe-preflight.v1",
            "request_sha256": request.sha256,
            "fixed_input_sha256": digest(fixed),
            "runtime_profile_sha256": runtime_sha,
            "model_binding_sha256": config.model_binding.sha256,
        }
    )
    submission = {
        "id": str(uuid4()),
        "owner_id": str(owner),
        "run_id": str(runid),
        "origin": "workflow_stage",
        "request_canonical": canonical_bytes(request).decode(),
        "request_sha256": request.sha256,
        "preflight_sha256": preflight,
        "config_sha256": config.sha256,
    }
    run = {
        "id": str(runid),
        "owner_id": str(owner),
        "config": config.model_dump(mode="json"),
    }
    spec = {
        "run_id": str(runid),
        "owner_id": str(owner),
        "submission_id": submission["id"],
        "request": request.model_dump(mode="json"),
        "request_canonical": submission["request_canonical"],
        "config": run["config"],
        "config_canonical": canonical_bytes(config).decode(),
        "config_sha256": config.sha256,
        "preflight_sha256": preflight,
        "request_sha256": request.sha256,
    }
    row = dict(
        id=str(uuid4()),
        **scope,
        stage_id=str(sid),
        input_revision_id=str(rid),
        run_id=str(runid),
        submission_id=submission["id"],
        submission_origin="workflow_stage",
        request_sha256=request.sha256,
        config_sha256=config.sha256,
        runtime_profile_sha256=runtime_sha,
        definition_sha256=definition.sha256,
        selection_sha256=selection.sha256,
        resolved_inputs_sha256=context.inputs.sha256,
        fixed_inputs_sha256=digest(fixed),
        preflight_sha256=preflight,
        **{
            key: (
                getattr(context, key)
                if key == "source_generation" or key.endswith("sha256")
                else str(getattr(context, key))
            )
            for key in (
                "rd_checkpoint_sha256",
                "rd_stage_id",
                "rd_checkpoint_id",
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "snapshot_id",
                "factor_version_id",
                "analysis_id",
                "release_exposure_id",
            )
        },
    )
    metadata = {
        "indexes": {
            "research_workflows": {str(wid): {"id": str(wid), "owner_id": str(owner)}},
            "research_workflow_stages": {str(sid): stage},
            "research_workflow_revisions": {str(rid): revision},
        },
        "selections": {str(rid): selection},
        "definitions": {str(sid): definition},
        "plans": {str(wid): plan},
    }
    return (
        row,
        metadata,
        {
            "config": config,
            "request": request,
            "spec": spec,
            "submission": submission,
            "run": run,
        },
    )


def test_actual_typed_contracts_bind_without_current_settings(declaration):
    row, metadata, args = declaration
    validate_submission_binding(row, metadata, **args)


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "submission_id",
        "source_run_id",
        "source_attempt_id",
        "snapshot_id",
        "factor_version_id",
        "analysis_id",
        "release_exposure_id",
        "request_sha256",
        "config_sha256",
        "preflight_sha256",
        "runtime_profile_sha256",
        "definition_sha256",
        "selection_sha256",
        "resolved_inputs_sha256",
        "rd_checkpoint_sha256",
        "fixed_inputs_sha256",
    ],
)
def test_each_immutable_attachment_field_is_bound(declaration, field):
    row, metadata, args = declaration
    row[field] = "f" * 64 if field.endswith("sha256") else str(uuid4())
    with pytest.raises((ValueError, KeyError)):
        validate_submission_binding(row, metadata, **args)


@pytest.mark.parametrize("target", ["row", "submission"])
def test_user_origin_cannot_be_relabelled_as_workflow(declaration, target):
    row, metadata, args = declaration
    (row if target == "row" else args["submission"])[
        "submission_origin" if target == "row" else "origin"
    ] = "authenticated_user_submission"
    with pytest.raises(ValueError):
        validate_submission_binding(row, metadata, **args)


@pytest.mark.parametrize("generation", [True, 1.0, "1", 0])
def test_source_generation_is_not_coercible(declaration, generation):
    row, metadata, args = declaration
    row["source_generation"] = generation
    with pytest.raises(ValueError):
        validate_submission_binding(row, metadata, **args)


def test_consistently_rebound_rows_cannot_move_frozen_child(declaration):
    row, metadata, args = declaration
    new_run = str(uuid4())
    row["run_id"] = new_run
    args["run"]["id"] = new_run
    args["submission"]["run_id"] = new_run
    args["spec"]["run_id"] = new_run
    metadata["indexes"]["research_workflow_stages"][row["stage_id"]]["child_run_id"] = (
        new_run
    )
    with pytest.raises(ValueError):
        validate_submission_binding(row, metadata, **args)
