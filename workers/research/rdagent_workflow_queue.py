"""Explicit028 workflow child delivery/claim checks; no alternative queue engine.

The ordinary RD dispatcher and task call this at their versioned branch. Delivery
checks metadata only; actual attempt claim must reread all retained inputs under
its existing shared storage lifecycle. Neither path reads active input heads.
"""

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.rdagent_provider_profile import (
    resolve_provider_declaration,
    resolve_provider_runtime,
)
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_workflow_capability import require_workflow_revision
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_saved import (
    verify_workflow_submission,
    verify_workflow_submission_metadata,
)
from backend.app.services import DomainError
from packages.rdagent_adapter.workflow_identity import require_workflow_source_identity


def require_workflow_queue_binding(session, run, event, settings, *, read_inputs):
    if type(read_inputs) is not bool:
        raise ValueError("Exact workflow queue phase required")
    require_workflow_revision(session)
    config = require_workflow_source_identity(run)
    if (event.run_id, event.generation, run.generation) != (run.id, 1, 1):
        raise DomainError(
            "rd_dispatch_binding", "Exact workflow child outbox required", 409
        )
    row = session.scalar(
        select(FactorWorkflowSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )
    if row is None:
        raise DomainError(
            "rd_dispatch_binding", "Committed workflow child receipt required", 409
        )
    for legacy in (FactorResearchSubmission, FactorSeededSubmission):
        if (
            session.scalar(
                select(legacy.id).filter_by(owner_id=run.owner_id, run_id=run.id)
            )
            is not None
        ):
            raise DomainError(
                "rd_dispatch_binding", "Exactly one submission origin required", 409
            )
    verified = (
        verify_workflow_submission(session, run.owner_id, row.id, settings)
        if read_inputs
        else verify_workflow_submission_metadata(session, run.owner_id, row.id)
    )
    if verified.run_id != run.id or verified.config_sha256 != config.sha256:
        raise DomainError("rd_dispatch_binding", "Workflow child binding differs", 409)
    profile = resolve_provider_runtime(settings)
    _, binding = resolve_provider_declaration(
        settings, run.owner_id, config.model_binding.profile_id
    )
    analysis = session.get(FactorAnalysisPlan, row.plan_id)
    if (
        analysis is None
        or analysis.owner_id != run.owner_id
        or analysis.run_id != run.id
        or profile.runtime != config.runtime
        or binding != config.model_binding
        or profile.qlib_installed_parent_image_id
        != analysis.qlib_installed_parent_image_id
    ):
        raise DomainError(
            "runtime_profile_changed",
            "Queued workflow runtime/model declaration changed",
            409,
        )
    return config, verified
