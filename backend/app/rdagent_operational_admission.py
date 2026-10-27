"""Flush-only one-shot protected controller claim; not a public launch issuer.

Caller commits the claim before external creation, holds process maintenance
protection across I/O, and rechecks under a fresh fence immediately before create.
An uncertain claim never grants a second create. Lost-fence containment/recovery
is separate; no rollback or filesystem cleanup is performed here.
"""

import json
import time

from backend.app.models import utcnow
from backend.app.rdagent_controller_process import (
    _persisted,
    record_controller_identity,
)
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext
from runtimes.rdagent_operational.process_journal import (
    ControllerIntent,
    read_controller_journal,
)


def _intent(admission, receipts):
    record = receipts.get("launch_intent")
    if record is None:
        raise ProcessError("rd_controller_claim_intent")
    intent = ControllerIntent.model_validate_json(json.dumps(record.payload))
    if intent.admission != admission:
        raise ProcessError("rd_controller_claim_intent")
    return intent


def _current(intent):
    now = utcnow()
    if not intent.issued_at <= now < intent.not_after:
        raise ProcessError("rd_controller_claim_expired")
    return now


def claim_controller_launch(session, fence, admission_id):
    admission, state, receipts = _persisted(session, fence, admission_id)
    intent = _intent(admission, receipts)
    if state.status != "launch_intended" or set(receipts) != {"launch_intent"}:
        raise ProcessError("rd_controller_already_claimed")
    now = _current(intent)
    state.status = "uncertain"
    state.updated_at = now
    session.flush()
    return intent


def check_controller_claim(session, fence, admission_id, *, expected_intent):
    admission, state, receipts = _persisted(session, fence, admission_id)
    intent = _intent(admission, receipts)
    if (
        state.status != "uncertain"
        or set(receipts) != {"launch_intent"}
        or type(expected_intent) is not ControllerIntent
        or ControllerIntent.model_validate(expected_intent) != intent
    ):
        raise ProcessError("rd_controller_claim_changed")
    _current(intent)
    return intent


def record_claimed_controller_identity(session, fence, admission_id, *, storage_root):
    admission, state, receipts = _persisted(session, fence, admission_id)
    intent = _intent(admission, receipts)
    if "launch_identity" in receipts:
        # Existing ingestion verifies exact immutable journals; it deliberately
        # preserves uncertain/stopped states, even if the old leader is live.
        return record_controller_identity(
            session, fence, admission_id, storage_root=storage_root
        )
    if state.status != "uncertain" or set(receipts) != {"launch_intent"}:
        raise ProcessError("rd_controller_claim_changed")
    _current(intent)
    launch, identity, _, _ = read_controller_journal(storage_root, admission)
    observer = DaemonIdentityContext(
        identity.container_id,
        identity.image_id,
        identity.leader.host_pid,
        time.monotonic() + (intent.not_after - utcnow()).total_seconds(),
    )
    if (
        launch.intent != intent
        or observer.observe(identity.leader.host_pid) != identity.leader
    ):
        raise ProcessError("rd_controller_claim_leader_changed")
    result = record_controller_identity(
        session, fence, admission_id, storage_root=storage_root
    )
    # Same locked transaction: only a first identity with a fresh kernel read can
    # promote the one-shot uncertain claim. Historical receipt replay cannot.
    state.status = "running"
    state.updated_at = utcnow()
    session.flush()
    return result
