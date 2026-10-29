"""Exact persisted workflow attachment at every neutral/physical OE boundary.

This verifies immutable relational identity, not physical source/release bytes.
Submission, queue claim and actual model requests additionally perform that full
verification under the existing storage guard.
"""

from sqlalchemy import select

from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.research_workflow_models import (
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from packages.factors.contracts import digest
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig


def require_attachment(session, run, spec, config):
    require_structured_workflow_schema(session)
    if type(config) is not WorkflowStructuredRunConfig:
        raise ValueError("Exact workflow OE config required")
    rows = session.scalars(
        select(OEWorkflowSubmission)
        .filter_by(owner_id=run.owner_id, run_id=run.id)
        .limit(2)
    ).all()
    if len(rows) != 1:
        raise ValueError("Unique actual workflow OE attachment required")
    row = rows[0]
    ctx = config.workflow_context
    bound = ctx.inputs.binding
    stage = session.get(ResearchWorkflowStage, row.stage_id)
    revision = session.get(ResearchWorkflowRevision, row.input_revision_id)
    submission = session.get(OEStructuredSubmission, row.submission_id)
    expected = {
        "owner_id": run.owner_id,
        "run_id": run.id,
        "workflow_id": bound.workflow_id,
        "stage_id": bound.stage_id,
        "input_revision_id": bound.input_revision_id,
        "config_sha256": config.sha256,
        "request_sha256": config.workflow_request_sha256,
        "definition_sha256": ctx.definition_sha256,
        "selection_sha256": ctx.inputs.selection.sha256,
        "resolved_inputs_sha256": ctx.inputs.sha256,
        "fixed_inputs_sha256": digest(config.fixed_inputs),
        "rd_stage_id": ctx.rd_stage_id,
        "rd_checkpoint_id": ctx.rd_checkpoint_id,
        "rd_checkpoint_sha256": ctx.rd_checkpoint_sha256,
        "source_run_id": ctx.source_run_id,
        "source_attempt_id": ctx.source_attempt_id,
        "source_generation": ctx.source_generation,
        "snapshot_id": ctx.snapshot_id,
        "factor_version_id": ctx.factor_version_id,
        "analysis_id": ctx.analysis_id,
        "release_exposure_id": ctx.release_exposure_id,
        "submission_origin": "workflow_stage",
    }
    if any(getattr(row, key) != value for key, value in expected.items()):
        raise ValueError("Immutable workflow OE attachment differs")
    if (
        stage is None
        or revision is None
        or submission is None
        or (
            stage.owner_id,
            stage.workflow_id,
            stage.child_run_id,
            stage.input_revision_id,
            stage.definition_sha256,
            stage.kind,
        )
        != (
            run.owner_id,
            bound.workflow_id,
            run.id,
            bound.input_revision_id,
            ctx.definition_sha256,
            "structured_factor_execution",
        )
        or (revision.owner_id, revision.workflow_id, revision.inputs_sha256)
        != (run.owner_id, bound.workflow_id, ctx.inputs.selection.sha256)
        or (
            submission.owner_id,
            submission.run_id,
            submission.origin,
            submission.request_sha256,
        )
        != (run.owner_id, run.id, "workflow_stage", config.workflow_request_sha256)
        or spec.run_id != row.run_id
    ):
        raise ValueError("Workflow OE stage/submission relation differs")
    return row
