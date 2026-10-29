"""Revocation-first normal027 recovery; no retry, result, refund or success."""

from uuid import uuid4

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import reconcile_attempt_units
from backend.app.services import append_event
from packages.research_runtime.process_contracts import ProcessError

REVISION = "028_oe_structured_search"
CONTROLLER = "mineagent.oe-structured-orchestrator.v1"
ENGINE = "mineagent.factor-strategy-engine.v1"


def _locked(session, attempt_id):
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
    if (
        run is None
        or attempt is None
        or attempt.finished_at is not None
        or run.active_attempt_id != attempt.id
        or run.generation != attempt.generation
        or run.status not in {"running", "cancel_requested"}
    ):
        return None
    spec, _config = decode_spec(session, run)
    submission = session.get(OEStructuredSubmission, spec.submission_id)
    if submission is None or (
        submission.owner_id,
        submission.run_id,
        submission.idempotency_key,
        submission.request_sha256,
        submission.config_sha256,
        submission.request_canonical,
        submission.preflight_sha256,
    ) != (
        run.owner_id,
        run.id,
        run.idempotency_key,
        spec.request_sha256,
        spec.config_sha256,
        spec.request_canonical,
        spec.preflight_sha256,
    ):
        raise ProcessError("oe_structured_recovery_submission")
    return run, attempt


def _inventory(session, run, attempt):
    rows = session.scalars(
        select(ResearchProcessAdmission)
        .filter_by(
            owner_id=run.owner_id,
            run_id=run.id,
            attempt_id=attempt.id,
            generation=attempt.generation,
        )
        .limit(5)
    ).all()
    # One controller plus seed and at most two generated candidate engines.
    if (
        len(rows) > 4
        or any(
            row.schema_version != "mineagent.process-admission.v2"
            or row.profile not in {CONTROLLER, ENGINE}
            for row in rows
        )
        or sum(row.profile == CONTROLLER for row in rows) > 1
        or (
            any(row.profile == ENGINE for row in rows)
            and not any(row.profile == CONTROLLER for row in rows)
        )
        or len({row.id for row in rows}) != len(rows)
    ):
        raise ProcessError("oe_structured_recovery_inventory")
    return frozenset((row.id, row.profile) for row in rows)


def _recover(factory, settings, admission_id, profile):
    if profile == CONTROLLER:
        from workers.research.oe_structured_controller_recovery import (
            recover_controller_unit,
        )

        return recover_controller_unit(factory, settings, admission_id)
    if profile == ENGINE:
        from workers.research.oe_structured_engine_recovery import recover_engine_unit

        return recover_engine_unit(factory, settings, admission_id)
    raise ProcessError("oe_structured_recovery_profile")


def _provider_stopped(session, run, attempt, settings):
    from backend.app.oe_structured_provider_containment import provider_attempt_stopped

    return provider_attempt_stopped(session, run, attempt, settings)


def reconcile_structured_attempt(factory, attempt_id, settings):
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with factory.begin() as session:
            if session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all() not in (
                [REVISION],
                ["029_research_workflow"],
                ["030_declared_upload_basket"],
                ["031_workflow_structured_stage"],
                ["032_normal_oe_note_pins"],
            ):
                return False
            pair = _locked(session, attempt_id)
            if pair is None:
                return False
            run, attempt = pair
            if run.status != "cancel_requested" and attempt.lease_expires_at > utcnow():
                return False
            admissions = _inventory(session, run, attempt)
            token = uuid4()
            attempt.lease_token = token
            attempt.lease_expires_at = utcnow()
        # No SQL transaction or borrowed native guard spans external containment.
        for admission_id, profile in sorted(admissions, key=lambda item: str(item[0])):
            try:
                if not _recover(factory, settings, admission_id, profile):
                    return False
            except Exception:  # noqa: BLE001 -- unknown remains held/nonterminal
                return False
        with native_storage_guard(settings.storage_root), factory.begin() as session:
            pair = _locked(session, attempt_id)
            if pair is None:
                return False
            run, attempt = pair
            if (
                attempt.lease_token != token
                or attempt.lease_expires_at > utcnow()
                or _inventory(session, run, attempt) != admissions
            ):
                return False
            summary = reconcile_attempt_units(
                session, run.id, attempt.id, attempt.generation
            )
            if not summary.confirmed or not _provider_stopped(
                session, run, attempt, settings
            ):
                return False
            attempt.sandbox_stopped = True
            attempt.finished_at = utcnow()
            attempt.status = run.status = (
                "cancelled" if run.status == "cancel_requested" else "interrupted"
            )
            attempt.error = run.error = "oe_structured_recovered_without_result_import"
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
