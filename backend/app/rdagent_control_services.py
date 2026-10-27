"""Owner-scoped cancellation; requesting stop is never evidence of physical stop."""

from sqlalchemy import select, text

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_services import REVISION
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_services import TERMINAL
from backend.app.services import DomainError, append_event
from packages.rdagent_adapter.persisted_identity import require_source_identity


def controls_for(session, run):
    from backend.app.engine_run_schemas import DisabledActions

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        [REVISION],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
    ):
        return DisabledActions()
    receipt = session.scalar(
        select(FactorResearchSubmission.id).filter_by(
            owner_id=run.owner_id, run_id=run.id
        )
    )
    if receipt is None:
        from backend.app.rdagent_seed_boundary import seeded_boundary_id

        receipt = seeded_boundary_id(session, run, revisions)
    if receipt is None:
        return DisabledActions()
    return DisabledActions(
        cancel=run.status in {"queued", "preparing", "running", "finalizing"},
        reason_code="cancel_pending_containment"
        if run.status == "cancel_requested"
        else "fresh_profile_pause_resume_not_supported",
    )


def request_cancel(session, owner_id, run_id):
    from backend.app.engine_run_submission_schemas import RDControlReceipt

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if revisions not in (
        [REVISION],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
    ):
        raise DomainError(
            "engine_control_not_enabled", "RD control schema is unavailable", 409
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .filter_by(owner_id=owner_id, id=run_id)
        .with_for_update(nowait=True)
    )
    if run is None:
        raise DomainError("not_found", "Research run not found", 404)
    try:
        require_source_identity(run)
    except ValueError:
        raise DomainError("not_found", "Research run not found", 404) from None
    receipt = session.scalar(
        select(FactorResearchSubmission.id).filter_by(owner_id=owner_id, run_id=run_id)
    )
    if receipt is None:
        from backend.app.rdagent_seed_boundary import seeded_boundary_id

        receipt = seeded_boundary_id(session, run, revisions)
    if receipt is None:
        raise DomainError(
            "engine_control_not_enabled",
            "This historical run has no submitted control boundary",
            409,
        )
    if run.status == "queued":
        if (
            run.active_attempt_id is not None
            or session.scalar(
                select(ExecutionAttempt.id).filter_by(run_id=run_id).limit(1)
            )
            is not None
        ):
            raise DomainError(
                "engine_control_drift", "Queued execution state is inconsistent", 409
            )
        run.status = "cancelled"
        run.updated_at = utcnow()
        append_event(
            session, run, "rd_cancelled_before_claim", {"status": "cancelled"}, None
        )
    elif run.status in {"preparing", "running", "finalizing"}:
        run.status = "cancel_requested"
        run.updated_at = utcnow()
        append_event(
            session,
            run,
            "rd_cancel_requested",
            {"status": "cancel_requested", "physical_stop_proven": False},
            run.active_attempt_id,
        )
    elif run.status == "paused" or run.status not in TERMINAL | {"cancel_requested"}:
        raise DomainError(
            "engine_control_not_enabled",
            "This fresh-only profile does not support checkpoint continuation",
            409,
        )
    session.flush()
    return RDControlReceipt(
        run_id=run.id, status=run.status, pending=run.status == "cancel_requested"
    )
