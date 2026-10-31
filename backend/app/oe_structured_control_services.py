"""Normal027 owner cancellation intent, never physical stop or budget release.

Flush-only: the authenticated route owns the surrounding transaction/commit.
No provider readiness, live fence, process kill, pause or resume is granted here.
"""

import json

from sqlalchemy import select, text

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.oe_structured_identity import decode_spec, fail
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_provider_submission import ProviderStructuredSearchSubmit
from backend.app.oe_structured_run_schemas import (
    StructuredControlReceipt,
    StructuredRunActions,
)
from backend.app.oe_structured_submission_receipts import existing_receipt
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_services import TERMINAL
from backend.app.services import DomainError, append_event

REVISION = "028_oe_structured_search"
ACTIVE = {"queued", "preparing", "running", "finalizing"}


def _installed(session):
    return session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
        [REVISION],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )


def _submitted(session, run):
    spec, _ = decode_spec(session, run)
    declaration = session.get(OEStructuredSubmission, spec.submission_id)
    if declaration is None or (declaration.owner_id, declaration.run_id) != (
        run.owner_id,
        run.id,
    ):
        fail()
    try:
        submitted = ProviderStructuredSearchSubmit.model_validate_json(
            json.dumps(
                {**spec.request, "preflight_sha256": spec.preflight_sha256},
                allow_nan=False,
            )
        )
    except (TypeError, ValueError):
        fail()
    receipt = existing_receipt(
        session, run.owner_id, declaration.idempotency_key, submitted
    )
    if receipt is None or (receipt.id, receipt.run_id) != (declaration.id, run.id):
        fail()


def controls_for(session, run):
    if not _installed(session):
        return StructuredRunActions()
    _submitted(session, run)
    return StructuredRunActions(
        cancel=run.status in ACTIVE,
        reason_code="cancel_pending_containment"
        if run.status == "cancel_requested"
        else "structured_pause_resume_not_supported",
    )


def request_cancel(session, owner_id, run_id):
    if not _installed(session):
        raise DomainError(
            "engine_control_not_enabled",
            "Normal structured control schema is unavailable",
            409,
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .filter_by(owner_id=owner_id, id=run_id)
        .with_for_update(nowait=True)
    )
    if run is None or (run.owner_id, run.id) != (owner_id, run_id):
        raise DomainError("run_not_found", "Structured search run not found", 404)
    _submitted(session, run)
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
            session,
            run,
            "oe_structured_cancelled_before_claim",
            {"status": "cancelled"},
            None,
        )
    elif run.status in ACTIVE - {"queued"}:
        run.status = "cancel_requested"
        run.updated_at = utcnow()
        append_event(
            session,
            run,
            "oe_structured_cancel_requested",
            {"status": "cancel_requested", "physical_stop_proven": False},
            run.active_attempt_id,
        )
    elif run.status == "paused" or run.status not in TERMINAL | {"cancel_requested"}:
        raise DomainError(
            "engine_control_not_enabled",
            "Normal structured search does not support pause or resume",
            409,
        )
    session.flush()
    return StructuredControlReceipt(
        run_id=run.id, status=run.status, pending=run.status == "cancel_requested"
    )
