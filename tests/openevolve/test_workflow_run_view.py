"""Actual run/spec/attachment codecs over explicit DB row doubles, not PG proof."""

from types import SimpleNamespace as NS

import pytest

from backend.app.oe_structured_run_views import read_owned_run
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.research_workflow_models import (
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.openevolve.test_structured_run_views import view_graph
from tests.openevolve.test_workflow_run import config as context_config


def arranged():
    session, run, declaration, _budget = view_graph()
    from backend.app.oe_structured_models import OEStructuredSpec

    spec = session.get(OEStructuredSpec, run.id)
    raw = dict(run.config)
    context = context_config()["workflow_context"]
    context.update(
        child_run_id=str(run.id),
        snapshot_id=raw["fixed_inputs"]["snapshot_id"],
        snapshot_manifest_sha256=raw["fixed_inputs"]["snapshot_manifest_sha256"],
        factor_version_id=raw["fixed_inputs"]["factor_version_id"],
        release_exposure_id=raw["fixed_inputs"]["release_receipt_ids"][0],
    )
    context["inputs"]["binding"]["owner_id"] = str(run.owner_id)
    context["inputs"]["selection"]["owner_id"] = str(run.owner_id)
    from packages.research_workflow.contracts import WorkflowInputSnapshot

    selection = WorkflowInputSnapshot.model_validate_json(
        canonical_bytes(context["inputs"]["selection"])
    )
    context["inputs"]["binding"]["input_sha256"] = selection.sha256
    request = WorkflowStructuredSearchRequest.model_validate_json(
        canonical_bytes(
            {
                **spec.request,
                "schema_version": "mineagent.oe-structured-workflow-submission.v1",
                "profile": "workflow_fixed_factor_operator_closed_program_v1",
                "workflow_context": context,
            }
        )
    )
    raw.pop("authenticated_request_sha256")
    raw.update(
        schema_version="mineagent.oe-structured-provider-run.v2",
        workflow_context=context,
        workflow_request_sha256=request.sha256,
    )
    config = WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(raw))
    run.config = config.model_dump(mode="json")
    run.request_sha256 = config.sha256
    spec.config, spec.config_canonical, spec.config_sha256 = (
        run.config,
        canonical_bytes(config).decode(),
        config.sha256,
    )
    spec.request, spec.request_canonical, spec.request_sha256 = (
        request.model_dump(mode="json"),
        canonical_bytes(request).decode(),
        request.sha256,
    )
    declaration.origin = "workflow_stage"
    declaration.request_sha256 = request.sha256
    ctx = config.workflow_context
    binding = ctx.inputs.binding
    attachment = OEWorkflowSubmission(
        owner_id=run.owner_id,
        run_id=run.id,
        workflow_id=binding.workflow_id,
        stage_id=binding.stage_id,
        input_revision_id=binding.input_revision_id,
        submission_id=declaration.id,
        config_sha256=config.sha256,
        request_sha256=request.sha256,
        preflight_sha256=spec.preflight_sha256,
        definition_sha256=ctx.definition_sha256,
        selection_sha256=ctx.inputs.selection.sha256,
        resolved_inputs_sha256=ctx.inputs.sha256,
        fixed_inputs_sha256=digest(config.fixed_inputs),
        submission_origin="workflow_stage",
        **{
            key: getattr(ctx, key)
            for key in (
                "rd_stage_id",
                "rd_checkpoint_id",
                "rd_checkpoint_sha256",
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
    stage = ResearchWorkflowStage(
        id=binding.stage_id,
        owner_id=run.owner_id,
        workflow_id=binding.workflow_id,
        child_run_id=run.id,
        input_revision_id=binding.input_revision_id,
        definition_sha256=ctx.definition_sha256,
        kind="structured_factor_execution",
    )
    revision = ResearchWorkflowRevision(
        id=binding.input_revision_id,
        owner_id=run.owner_id,
        workflow_id=binding.workflow_id,
        inputs_sha256=ctx.inputs.selection.sha256,
    )
    old_get = session.get
    extra = {
        (ResearchWorkflowStage, stage.id): stage,
        (ResearchWorkflowRevision, revision.id): revision,
    }
    session.get = lambda cls, key: extra.get((cls, key)) or old_get(cls, key)
    session.scalars = lambda query: NS(
        all=lambda: (
            ["032_normal_oe_note_pins"]
            if str(query) == "SELECT version_num FROM alembic_version"
            else [attachment]
        )
    )
    return session, run, declaration, attachment


def test_workflow_run_view_is_distinct_metadata_without_user_receipt_or_release():
    session, run, declaration, attachment = arranged()
    result = read_owned_run(session, run.owner_id, run.id)
    assert result.schema_version == "mineagent.oe-structured-run-view.v2"
    assert result.submission.origin == "workflow_stage"
    assert result.submission.owner_id == run.owner_id
    assert result.submission.workflow_id == attachment.workflow_id
    assert result.submission.id == declaration.id
    assert "origin_user_id" not in result.submission.model_dump()
    assert "normalized_request" not in result.submission.model_dump()
    assert (
        result.actions.cancel is result.actions.pause is result.actions.resume is False
    )
    assert not ({"measurement", "execution", "result"} & result.model_dump().keys())


@pytest.mark.parametrize(
    "field", ["owner_id", "run_id", "submission_id", "stage_id", "preflight_sha256"]
)
def test_workflow_view_rejects_wrong_attachment(field):
    from uuid import uuid4

    session, run, _, attachment = arranged()
    setattr(
        attachment,
        field,
        ("0" if getattr(attachment, field) != "0" * 64 else "1") * 64
        if field.endswith("sha256")
        else uuid4(),
    )
    with pytest.raises((DomainError, ValueError)):
        read_owned_run(session, run.owner_id, run.id)


def test_workflow_view_cannot_be_relabelled_authenticated_submission():
    session, run, declaration, _ = arranged()
    declaration.origin = "authenticated_user_submission"
    with pytest.raises((DomainError, ValueError)):
        read_owned_run(session, run.owner_id, run.id)
