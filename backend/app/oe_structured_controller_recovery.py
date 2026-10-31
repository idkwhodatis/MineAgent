"""Flush-only stale-fence containment ingestion; never execution/result authority."""

import hashlib

from sqlalchemy import select, text

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.research_process_services import _receipt
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_structured_host.process_journal import (
    controller_admission,
    read_controller_journal,
)


def record_controller_containment(session, admission_id, *, storage_root):
    """Caller retains maintenance admission; do not require/revive a stale lease.

    Only this old admission's physical containment is recorded. There is no budget
    settlement/refund, result import, new-generation mutation or running promotion.
    """
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.oe_structured_identity import decode_spec
    from backend.app.oe_structured_operational_models import OEStructuredEpoch

    require_native_storage_guard(storage_root)
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        raise ProcessError("oe_structured_controller_recovery_revision")
    row = session.get(ResearchProcessAdmission, admission_id)
    if row is None:
        raise ProcessError("oe_structured_controller_process_missing")
    admission = controller_admission(
        decode_row({k: getattr(row, k) for k in ROW_FIELDS})
    )
    _try_neutral_lock(session, f"submit:{admission.owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.id == admission.run_id,
            ResearchRun.owner_id == admission.owner_id,
        )
        .with_for_update(nowait=True)
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.id == admission.attempt_id,
            ExecutionAttempt.run_id == admission.run_id,
            ExecutionAttempt.generation == admission.generation,
        )
        .with_for_update(nowait=True)
    )
    if run is None or attempt is None:
        raise ProcessError("oe_structured_controller_recovery_scope")
    _, config = decode_spec(session, run)
    epoch = session.get(OEStructuredEpoch, admission.logical_operation_id)
    if (
        epoch is None
        or epoch.controller_admission_id != admission.id
        or any(
            getattr(epoch, k) != getattr(admission, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        )
        or config.runtime.controller_image_id != admission.image_id
    ):
        raise ProcessError("oe_structured_controller_recovery_epoch")
    locked = session.get(
        ResearchProcessAdmission, admission_id, with_for_update={"nowait": True}
    )
    if (
        locked is None
        or decode_row({k: getattr(locked, k) for k in ROW_FIELDS}) != admission
    ):
        raise ProcessError("oe_structured_controller_recovery_admission_changed")
    state = session.get(
        ResearchProcessState, admission_id, with_for_update={"nowait": True}
    )
    keys = ("owner_id", "run_id", "attempt_id", "generation")
    if (
        state is None
        or state.status not in {"launch_intended", "uncertain", "running", "stopped"}
        or any(getattr(state, k) != getattr(admission, k) for k in keys)
    ):
        raise ProcessError("oe_structured_controller_recovery_state")
    receipts = {}
    for receipt in session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission_id)
    ).all():
        if (
            receipt.kind in receipts
            or any(getattr(receipt, k) != getattr(admission, k) for k in keys)
            or receipt.payload_sha256 != digest(receipt.payload)
        ):
            raise ProcessError("oe_structured_controller_recovery_receipt")
        receipts[receipt.kind] = receipt
    if "launch_intent" not in receipts or not set(receipts) <= {
        "launch_intent",
        "launch_identity",
        "controller_child",
        "stop",
        "controller_completion",
    }:
        raise ProcessError("oe_structured_controller_recovery_receipt")
    launch, identity, child, stop = read_controller_journal(
        storage_root, admission, with_stop=True
    )
    if (
        type(epoch.bootstrap_canonical) is not str
        or hashlib.sha256(epoch.bootstrap_canonical.encode("ascii")).hexdigest()
        != epoch.bootstrap_sha256
        or launch.intent.bootstrap_sha256 != epoch.bootstrap_sha256
        or launch.intent.bootstrap_size_bytes
        != len(epoch.bootstrap_canonical.encode("ascii"))
    ):
        raise ProcessError("oe_structured_controller_recovery_bootstrap")
    if receipts["launch_intent"].payload != launch.intent.model_dump(mode="json"):
        raise ProcessError("oe_structured_controller_recovery_intent")
    _receipt(session, admission, "launch_identity", identity.model_dump(mode="json"))
    if child is not None:
        _receipt(session, admission, "controller_child", child.model_dump(mode="json"))
    elif "controller_child" in receipts:
        raise ProcessError("oe_structured_controller_recovery_child_missing")
    _receipt(session, admission, "stop", stop.model_dump(mode="json"))
    if "controller_completion" in receipts:
        from runtimes.openevolve_structured_host.completion_journal import (
            read_controller_completion,
        )

        completion = read_controller_completion(storage_root, admission)
        if receipts["controller_completion"].payload != completion.model_dump(
            mode="json"
        ):
            raise ProcessError("oe_structured_controller_recovery_completion")
    require_native_storage_guard(storage_root)
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()
    return stop
