"""RD-v2 persistence-only boundary, pending020 and real protected-host acceptance.

No API/dispatcher/launcher registration. Flush-only services read host journals;
no receipt from controller requests is accepted. Existing global process resource
locks and full conservative physical reservation precede admission. Supervisor
must retain shared_lifecycle across external creation/I/O/termination. Current
fence is required here; stale-fence containment recovery is a separate missing
route, not silently granted by a DTO or a stopped-state assertion.
"""

import hashlib

from sqlalchemy import select

from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.rdagent_adapter.controller_profile import (
    controller_profile,
    controller_wall_milliseconds,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    decode_row,
    encode_row,
)
from runtimes.rdagent_operational.process_journal import (
    STORAGE_FLOOR,
    ControllerIntent,
    controller_admission,
    read_controller_journal,
)


def _scope(value, fence):
    if any(
        getattr(value, k) != getattr(fence, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise ProcessError("rd_controller_process_scope")


def persist_controller_intent(session, fence, intent):
    """Persist discoverable immutable obligation; never return repeat launch grant."""
    from backend.app.rdagent_controller_services import expected_operational_bootstrap
    from backend.app.research_broker_services import require_reservation
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.research_process_services import _receipt

    require_neutral_transaction(session, fence, resource_scope="processes")
    intent = ControllerIntent.model_validate(intent)
    admission = intent.admission
    _scope(admission, fence)
    binding, bootstrap = expected_operational_bootstrap(session, fence)
    from packages.factors.contracts import canonical_bytes

    raw = canonical_bytes(bootstrap)
    if (
        admission.profile != controller_profile(bootstrap.config)
        or admission.logical_operation_id != binding.id
        or admission.image_id != bootstrap.config.runtime.orchestrator_image
        or intent.bootstrap_sha256 != hashlib.sha256(raw).hexdigest()
        or intent.bootstrap_size_bytes != len(raw)
    ):
        raise ProcessError("rd_controller_bootstrap_identity")
    require_reservation(
        session,
        fence,
        admission.reservation_id,
        purpose="process_unit",
        minimum=BudgetVector(
            process_slots=1,
            wall_milliseconds=controller_wall_milliseconds(admission.profile),
            storage_bytes=STORAGE_FLOOR,
        ),
    )
    previous = session.scalar(
        select(ResearchProcessAdmission).filter_by(
            owner_id=admission.owner_id,
            run_id=admission.run_id,
            attempt_id=admission.attempt_id,
            generation=admission.generation,
            role=admission.role,
            logical_operation_id=admission.logical_operation_id,
            launch_ordinal=admission.launch_ordinal,
        )
    )
    if previous is not None:
        raise ProcessError("research_process_already_admitted")
    session.add(ResearchProcessAdmission(**encode_row(admission)))
    session.flush()
    session.add(
        ResearchProcessState(
            admission_id=admission.id,
            owner_id=admission.owner_id,
            run_id=admission.run_id,
            attempt_id=admission.attempt_id,
            generation=admission.generation,
            status="launch_intended",
        )
    )
    _receipt(session, admission, "launch_intent", intent.model_dump(mode="json"))
    session.flush()
    return admission


def _persisted(session, fence, admission_id):
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence, resource_scope="processes")
    row = session.get(ResearchProcessAdmission, admission_id, with_for_update=True)
    if row is None:
        raise ProcessError("rd_controller_process_missing")
    admission = controller_admission(
        decode_row({k: getattr(row, k) for k in ROW_FIELDS})
    )
    _scope(admission, fence)
    state = session.get(ResearchProcessState, admission_id, with_for_update=True)
    if state is None:
        raise ProcessError("rd_controller_process_missing")
    _scope(state, fence)
    receipts = {}
    for receipt in session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission_id)
    ).all():
        _scope(receipt, fence)
        if receipt.kind in receipts or receipt.payload_sha256 != digest(
            receipt.payload
        ):
            raise ProcessError("rd_controller_process_receipt")
        receipts[receipt.kind] = receipt
    return admission, state, receipts


def _ingest(session, fence, admission_id, root, stage):
    from backend.app.models import utcnow
    from backend.app.research_process_services import _receipt

    admission, state, receipts = _persisted(session, fence, admission_id)
    if state.status not in {"launch_intended", "running", "uncertain", "stopped"}:
        raise ProcessError("rd_controller_process_state")
    child = stage == "child" or "controller_child" in receipts
    launch, identity, child_record, stop = read_controller_journal(
        root, admission, with_child=child, with_stop=stage == "stop"
    )
    if "launch_intent" not in receipts or receipts[
        "launch_intent"
    ].payload != launch.intent.model_dump(mode="json"):
        raise ProcessError("rd_controller_process_intent")
    # A stopped projection cannot acquire new child/RPC authority after the fact.
    required = "controller_child" if stage == "child" else "launch_identity"
    if state.status == "stopped" and stage != "stop" and required not in receipts:
        raise ProcessError("rd_controller_process_stopped")
    _receipt(session, admission, "launch_identity", identity.model_dump(mode="json"))
    if child_record is not None:
        _receipt(
            session, admission, "controller_child", child_record.model_dump(mode="json")
        )
    if stop is not None:
        _receipt(session, admission, "stop", stop.model_dump(mode="json"))
        state.status = "stopped"
    elif state.status == "launch_intended":
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
