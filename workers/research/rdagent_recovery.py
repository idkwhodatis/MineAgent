"""Fresh-only RD recovery: revoke, contain exact inventory, then mark terminal.

No restart, new generation, output import, settlement or refund. Unknown physical
state remains nonterminal and all previously held resource reservations remain.
"""

from uuid import uuid4

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import reconcile_attempt_units
from backend.app.services import append_event
from packages.rdagent_adapter.persisted_identity import require_source_identity


def _locked(session, attempt_id, *, revisions=None):
    identity = session.execute(
        select(ResearchRun.owner_id, ResearchRun.id)
        .join(ExecutionAttempt, ExecutionAttempt.run_id == ResearchRun.id)
        .where(ExecutionAttempt.id == attempt_id)
    ).one_or_none()
    if identity is None:
        return None
    owner_id, run_id = identity
    _try_neutral_lock(session, f"submit:{owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .filter_by(id=run_id, owner_id=owner_id)
        .with_for_update(nowait=True)
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .filter_by(id=attempt_id, run_id=run_id)
        .with_for_update(nowait=True)
    )
    require_source_identity(run)
    if (
        attempt is None
        or attempt.finished_at is not None
        or run.active_attempt_id != attempt.id
        or run.generation != attempt.generation
        or run.status in {"succeeded", "failed", "cancelled", "interrupted"}
    ):
        return None
    receipt = session.scalar(
        select(FactorResearchSubmission.id).filter_by(owner_id=owner_id, run_id=run_id)
    )
    if receipt is None:
        from backend.app.rdagent_seed_boundary import seeded_boundary_id

        receipt = seeded_boundary_id(session, run, revisions)
    if receipt is None:
        return None
    return run, attempt


def reconcile_rd_attempt(factory, attempt_id, settings):
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with factory.begin() as session:
            revisions = session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all()
            if revisions not in (
                ["023_rd_submissions"],
                ["024_openevolve_initial"],
                ["025_structured_factor_strategy"],
                ["026_rd_seeded_submissions"],
                ["027_rd_controller_v3"],
                ["027a_contract_repairs"],
            ):
                return False
            pair = _locked(session, attempt_id, revisions=revisions)
            if pair is None:
                return False
            run, attempt = pair
            if run.status != "cancel_requested" and attempt.lease_expires_at > utcnow():
                return False
            # Commit revocation before external containment. Every delayed issuer
            # rechecks the immutable old token before create/start/publication.
            attempt.lease_token = uuid4()
            attempt.lease_expires_at = utcnow()
            admissions = [
                (r.id, r.profile)
                for r in session.scalars(
                    select(ResearchProcessAdmission).filter_by(
                        owner_id=run.owner_id,
                        run_id=run.id,
                        attempt_id=attempt.id,
                        generation=attempt.generation,
                    )
                ).all()
            ]
        for admission_id, profile in admissions:
            try:
                if profile in (
                    "mineagent.rdagent-orchestrator.v2",
                    "mineagent.rdagent-orchestrator.v3",
                ):
                    from workers.research.rdagent_operational import (
                        recover_controller_unit,
                    )

                    recover_controller_unit(
                        factory, settings.storage_root, admission_id
                    )
                elif profile == "mineagent.qlib-direct-analysis.v1":
                    from workers.research.analysis_recovery import recover_analysis_unit

                    recover_analysis_unit(factory, settings.storage_root, admission_id)
                else:
                    from workers.research.numeric_process import recover_numeric_unit

                    recover_numeric_unit(factory, settings.storage_root, admission_id)
            except Exception:  # noqa: BLE001 -- unknown remains held, never inferred absent
                return False
        with factory.begin() as session:
            pair = _locked(session, attempt_id, revisions=revisions)
            if pair is None:
                return False
            run, attempt = pair
            summary = reconcile_attempt_units(
                session, run.id, attempt.id, attempt.generation
            )
            from backend.app.rdagent_provider_containment import (
                provider_attempt_stopped,
            )

            if not summary.confirmed or not provider_attempt_stopped(
                session, run, attempt, settings
            ):
                return False
            attempt.sandbox_stopped = True
            attempt.finished_at = utcnow()
            attempt.status = run.status = (
                "cancelled" if run.status == "cancel_requested" else "interrupted"
            )
            attempt.error = run.error = "rd_recovered_without_result_import"
            run.updated_at = utcnow()
            append_event(
                session,
                run,
                "run_finished",
                {
                    "status": run.status,
                    "error_code": run.error,
                    "reservations_retained": True,
                },
                attempt.id,
            )
            return True
