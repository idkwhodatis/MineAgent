"""Immutable workflow receipt boundary for control/recovery, never work authority."""

from sqlalchemy import select

from backend.app.rdagent_workflow_capability import require_workflow_revision
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_saved import verify_workflow_submission_metadata
from packages.rdagent_adapter.workflow_identity import require_workflow_source_identity


def workflow_boundary_id(session, run, revisions):
    if (
        revisions
        not in (
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
        )
        or run.config.get("schema_version") != "mineagent.factor-research.v3"
    ):
        return None
    require_workflow_revision(session)
    require_workflow_source_identity(run)
    from backend.app.rdagent_seed_models import FactorSeededSubmission
    from backend.app.rdagent_submission_models import FactorResearchSubmission

    for legacy in (FactorResearchSubmission, FactorSeededSubmission):
        if (
            session.scalar(
                select(legacy.id).filter_by(owner_id=run.owner_id, run_id=run.id)
            )
            is not None
        ):
            raise ValueError("Workflow control requires one distinct submission origin")
    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )
    if row is None:
        return None
    verified = verify_workflow_submission_metadata(session, run.owner_id, row.id)
    if verified.run_id != run.id:
        raise ValueError("Workflow control receipt differs")
    return verified.id
