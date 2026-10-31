"""Flush-only027 controller admission, one-shot claim and real journal ingestion.

Caller commits the uncertain claim before external creation, retains lifecycle
and native-storage guards, then freshly checks immediately before issuing the
supervisor's single-use authority. No receipt DTO, replay or exception authorizes
a launch/refund. Epoch/bootstrap construction belongs to the normal host issuer.
"""

import hashlib
import time

from sqlalchemy import select, text

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import utcnow
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_broker_services import require_reservation, settle_budget
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.research_process_services import _receipt
from packages.factors.contracts import canonical_bytes
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    decode_row,
    encode_row,
)
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.openevolve_structured.profile import MAX_WALL_MS, STORAGE_FLOOR
from runtimes.openevolve_structured_host.process_journal import (
    ControllerIntent,
    controller_admission,
    read_controller_journal,
)

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
KINDS = frozenset(
    {
        "launch_intent",
        "launch_identity",
        "controller_child",
        "stop",
        "controller_completion",
    }
)


def _fail(code="oe_structured_controller_process"):
    raise ProcessError(code)


def _current(session, fence):
    require_neutral_transaction(session, fence, resource_scope="processes")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        _fail("oe_structured_controller_revision")


def _scope(row, fence):
    if row is None or any(getattr(row, key) != getattr(fence, key) for key in SCOPE):
        _fail("oe_structured_controller_scope")


def _decode_intent(value):
    if type(value) is not ControllerIntent:
        _fail("oe_structured_controller_intent")
    return ControllerIntent.model_validate_json(value.model_dump_json())


def _held(session, fence, admission):
    reservation = require_reservation(
        session,
        fence,
        admission.reservation_id,
        purpose="process_unit",
        minimum=BudgetVector(
            process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
        ),
    )
    _scope(reservation, fence)
    if (
        reservation.state != "reserved"
        or reservation.logical_key
        != f"oe-structured-controller:{admission.logical_operation_id}"
    ):
        _fail("oe_structured_controller_reservation")
    if (
        session.scalar(
            select(ResearchBudgetSettlement).filter_by(reservation_id=reservation.id)
        )
        is not None
    ):
        _fail("oe_structured_controller_already_settled")
    return reservation


def _expected(session, fence, intent, settings):
    # Fixed host resolver, not an injectable callback or serialized grant.
    from backend.app.oe_structured_epoch_services import expected_controller_bootstrap
    from backend.app.oe_structured_identity import decode_spec

    run, _ = require_neutral_transaction(session, fence, resource_scope="processes")
    _, config = decode_spec(session, run)

    epoch, bootstrap, seed = expected_controller_bootstrap(
        session, fence, intent.admission.logical_operation_id, settings
    )
    _scope(epoch, fence)
    raw = canonical_bytes(bootstrap)
    if (
        intent.admission.image_id != config.runtime.controller_image_id
        or bootstrap.spec != config.search
        or bootstrap.model_binding != config.model_binding
        or bootstrap.adapter_patch_sha256 != config.runtime.controller_inventory_sha256
        or epoch.id != intent.admission.logical_operation_id
        or epoch.controller_admission_id != intent.admission.id
        or bootstrap.admission_id != intent.admission.id
        or bootstrap.epoch_id != epoch.id
        or type(seed) is not bytes
        or epoch.bootstrap_canonical != raw.decode("ascii")
        or epoch.bootstrap_sha256 != hashlib.sha256(raw).hexdigest()
        or intent.bootstrap_sha256 != epoch.bootstrap_sha256
        or intent.bootstrap_size_bytes != len(raw)
        or intent.seed_sha256 != hashlib.sha256(seed).hexdigest()
        or intent.seed_sha256 != bootstrap.spec.seed_candidate_sha256
        or intent.seed_size_bytes != len(seed)
        or (intent.issued_at, intent.not_after)
        != (bootstrap.issued_at, bootstrap.not_after)
    ):
        _fail("oe_structured_controller_bootstrap")
    return intent


def persist_controller_intent(session, fence, intent, settings):
    """Persist discoverable immutable obligation; duplicates never yield a grant."""
    _current(session, fence)
    intent = _decode_intent(intent)
    admission = controller_admission(intent.admission)
    _scope(admission, fence)
    if admission.launch_ordinal != 1:
        _fail("oe_structured_controller_ordinal")
    _expected(session, fence, intent, settings)
    _held(session, fence, admission)
    previous = session.scalar(
        select(ResearchProcessAdmission).filter_by(
            **{key: getattr(fence, key) for key in SCOPE},
            role=admission.role,
            logical_operation_id=admission.logical_operation_id,
            launch_ordinal=1,
        )
    )
    if previous is not None:
        _fail("oe_structured_controller_already_admitted")
    session.add(ResearchProcessAdmission(**encode_row(admission)))
    session.flush()
    session.add(
        ResearchProcessState(
            admission_id=admission.id,
            **{key: getattr(fence, key) for key in SCOPE},
            status="launch_intended",
        )
    )
    _receipt(session, admission, "launch_intent", intent.model_dump(mode="json"))
    session.flush()
    return admission


def _persisted(session, fence, admission_id):
    _current(session, fence)
    row = session.get(ResearchProcessAdmission, admission_id, with_for_update=True)
    _scope(row, fence)
    admission = controller_admission(
        decode_row({key: getattr(row, key) for key in ROW_FIELDS})
    )
    state = session.get(ResearchProcessState, admission_id, with_for_update=True)
    _scope(state, fence)
    rows = session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission_id).limit(6)
    ).all()
    receipts = {}
    for row in rows:
        _scope(row, fence)
        if (
            row.kind not in KINDS
            or row.kind in receipts
            or row.payload_sha256 != digest(row.payload)
        ):
            _fail("oe_structured_controller_receipt")
        receipts[row.kind] = row
    if "launch_intent" not in receipts:
        _fail("oe_structured_controller_missing_intent")
    intent = ControllerIntent.model_validate_json(
        canonical_bytes(receipts["launch_intent"].payload)
    )
    if intent.admission != admission:
        _fail("oe_structured_controller_intent")
    return admission, state, receipts, intent


def _unexpired(intent):
    now = utcnow()
    if not intent.issued_at <= now < intent.not_after:
        _fail("oe_structured_controller_expired")
    return now


def claim_controller_intent(session, fence, admission_id, settings):
    admission, state, receipts, intent = _persisted(session, fence, admission_id)
    _expected(session, fence, intent, settings)
    _held(session, fence, admission)
    if state.status != "launch_intended" or set(receipts) != {"launch_intent"}:
        _fail("oe_structured_controller_already_claimed")
    state.updated_at = _unexpired(intent)
    state.status = "uncertain"
    session.flush()
    return intent


def check_controller_claim(session, fence, admission_id, settings, *, expected_intent):
    admission, state, receipts, intent = _persisted(session, fence, admission_id)
    _expected(session, fence, intent, settings)
    _held(session, fence, admission)
    if (
        state.status != "uncertain"
        or set(receipts) != {"launch_intent"}
        or _decode_intent(expected_intent) != intent
    ):
        _fail("oe_structured_controller_claim_changed")
    _unexpired(intent)
    return intent


def _ingest(session, fence, admission_id, root, stage):
    require_native_storage_guard(root)
    admission, state, receipts, intent = _persisted(session, fence, admission_id)
    if state.status not in {"uncertain", "running", "stopped"}:
        _fail("oe_structured_controller_not_claimed")
    launch, identity, child, stop = read_controller_journal(
        root,
        admission,
        with_child=stage == "child" or "controller_child" in receipts,
        with_stop=stage == "stop",
    )
    if launch.intent != intent:
        _fail("oe_structured_controller_journal_intent")
    expected = {"launch_intent": intent, "launch_identity": identity}
    if child is not None:
        expected["controller_child"] = child
    if stop is not None:
        expected["stop"] = stop
    if "controller_completion" in receipts:
        from runtimes.openevolve_structured_host.completion_journal import (
            read_controller_completion,
        )

        expected["controller_completion"] = read_controller_completion(root, admission)
    if any(
        key not in expected or row.payload != expected[key].model_dump(mode="json")
        for key, row in receipts.items()
    ):
        _fail("oe_structured_controller_journal_drift")
    if state.status == "stopped" and set(expected) != set(receipts):
        _fail("oe_structured_controller_stopped_immutable")
    promote = stage == "identity" and "launch_identity" not in receipts
    if promote:
        if state.status != "uncertain" or set(receipts) != {"launch_intent"}:
            _fail("oe_structured_controller_claim_changed")
        now = _unexpired(intent)
        observer = DaemonIdentityContext(
            identity.container_id,
            identity.image_id,
            identity.leader.host_pid,
            time.monotonic() + (intent.not_after - now).total_seconds(),
        )
        if observer.observe(identity.leader.host_pid) != identity.leader:
            _fail("oe_structured_controller_leader_changed")
    if stage == "child" and state.status != "running":
        _fail("oe_structured_controller_child_not_running")
    # Fresh original fence after physical/kernel observation, before SQL writes.
    _current(session, fence)
    require_native_storage_guard(root)
    for kind, value in expected.items():
        _receipt(session, admission, kind, value.model_dump(mode="json"))
    if stop is not None:
        state.status = "stopped"
    elif promote:
        state.status = "running"
    state.updated_at = utcnow()
    session.flush()
    return admission


def record_controller_identity(session, fence, admission_id, *, storage_root):
    return _ingest(session, fence, admission_id, storage_root, "identity")


def record_controller_child(session, fence, admission_id, *, storage_root):
    return _ingest(session, fence, admission_id, storage_root, "child")


def record_controller_stopped(session, fence, admission_id, *, storage_root):
    return _ingest(session, fence, admission_id, storage_root, "stop")


def settle_stopped_controller(session, fence, admission_id, *, storage_root):
    """Only exact physical positive stop permits full conservative settlement."""
    admission = record_controller_stopped(
        session, fence, admission_id, storage_root=storage_root
    )
    held = session.get(
        ResearchBudgetReservation, admission.reservation_id, with_for_update=True
    )
    _scope(held, fence)
    minimum = BudgetVector(
        process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
    )
    if (
        held.logical_key != f"oe-structured-controller:{admission.logical_operation_id}"
        or held.purpose != "process_unit"
        or held.state not in {"reserved", "uncertain", "settled"}
        or not BudgetVector.model_validate(held.amounts).covers(minimum)
    ):
        _fail("oe_structured_controller_reservation")
    previous = session.scalar(
        select(ResearchBudgetSettlement).filter_by(reservation_id=held.id)
    )
    if (held.state == "settled") != (previous is not None):
        _fail("oe_structured_controller_settlement_state")
    if previous is not None:
        _scope(previous, fence)
        if (
            previous.outcome != "conservative_unknown"
            or previous.charge != held.amounts
        ):
            _fail("oe_structured_controller_settlement_drift")
    # Existing ledger returns its exact immutable receipt on repeated stop ACK;
    # physical stop and original full-charge relationship were rechecked above.
    return settle_budget(
        session,
        fence,
        held.id,
        charge=BudgetVector.model_validate(held.amounts),
        outcome="conservative_unknown",
    )


def record_controller_completion(session, fence, admission_id, *, storage_root):
    """Only actual positive-finish bytes, never Stop alone or caller observation."""
    from runtimes.openevolve_structured_host.completion_journal import (
        read_controller_completion,
    )

    require_native_storage_guard(storage_root)
    admission, state, receipts, intent = _persisted(session, fence, admission_id)
    if state.status != "stopped" or set(receipts) not in (
        {"launch_intent", "launch_identity", "controller_child", "stop"},
        set(KINDS),
    ):
        _fail("oe_structured_completion_before_stop")
    launch, identity, child, stop = read_controller_journal(
        storage_root, admission, with_child=True, with_stop=True
    )
    expected = {
        "launch_intent": intent,
        "launch_identity": identity,
        "controller_child": child,
        "stop": stop,
    }
    if launch.intent != intent or any(
        receipts[k].payload != value.model_dump(mode="json")
        for k, value in expected.items()
    ):
        _fail("oe_structured_completion_chain")
    value = read_controller_completion(storage_root, admission)
    _current(session, fence)
    require_native_storage_guard(storage_root)
    return _receipt(
        session, admission, "controller_completion", value.model_dump(mode="json")
    )
