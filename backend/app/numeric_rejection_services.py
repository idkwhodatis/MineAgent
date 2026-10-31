"""Current-fence protected rejection ingestion/read; no inferred code failures.

Installed revision 027 must explicitly register this new receipt grammar. This
module neither relaxes v1/v2 success paths nor grants admission or replay.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text

from backend.app.numeric_process_services import _persisted, _scope
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.numeric_process_journal import NumericInput
from packages.research_runtime.numeric_rejection import NumericRejection
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2


@dataclass(frozen=True)
class NumericRejectionReceipt:
    admission: ProcessDeclarationV2
    inputs: NumericInput
    launch_identity_receipt_id: UUID
    launch_identity_receipt_hash: str
    stop_receipt_id: UUID
    stop_receipt_hash: str
    rejection_receipt_id: UUID
    rejection_receipt_hash: str
    rejection: NumericRejection

    @property
    def admission_id(self):
        return self.admission.id

    @property
    def outcome(self):
        return self.rejection.outcome

    @property
    def child_exit_code(self):
        return self.rejection.child_exit_code


def require_numeric_rejection_schema(session, fence):
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence, resource_scope="processes")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    ):
        raise ProcessError("numeric_rejection_schema_unsupported")


def _physical(session, fence, admission_id, storage_root):
    from runtimes.research_supervisor.numeric_rejection import read_rejection_journal

    require_numeric_rejection_schema(session, fence)
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    launch, identity, stop, rejection = read_rejection_journal(storage_root, admission)
    # Retained files are never a substitute for a still-current transaction/fence.
    require_numeric_rejection_schema(session, fence)
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "stop": stop,
        "rejection": rejection,
    }
    if (
        "launch_intent" not in receipts
        or set(receipts) - set(expected)
        or any(
            receipt.payload != expected[kind].model_dump(mode="json")
            for kind, receipt in receipts.items()
        )
    ):
        raise ProcessError("numeric_rejection_receipt")
    return admission, state, receipts, expected


def record_numeric_rejection(session, fence, admission_id, *, storage_root):
    """Flush measured rejection and stop; leaves charging to explicit finalizer."""
    from backend.app.models import utcnow
    from backend.app.research_process_services import _receipt

    admission, state, _receipts, expected = _physical(
        session, fence, admission_id, storage_root
    )
    if state.status not in {"launch_intended", "running", "uncertain", "stopped"}:
        raise ProcessError("numeric_process_state")
    for kind in ("launch_identity", "stop", "rejection"):
        _receipt(session, admission, kind, expected[kind].model_dump(mode="json"))
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()


def read_numeric_rejection(session, fence, admission_id, *, storage_root):
    """Fresh persisted/physical proof; not a settlement or reusable capability."""
    admission, state, receipts, expected = _physical(
        session, fence, admission_id, storage_root
    )
    if state.status != "stopped" or set(receipts) != set(expected):
        raise ProcessError("numeric_rejection_uncommitted")
    return NumericRejectionReceipt(
        admission=admission,
        inputs=expected["launch_intent"].inputs,
        launch_identity_receipt_id=receipts["launch_identity"].id,
        launch_identity_receipt_hash=receipts["launch_identity"].payload_sha256,
        stop_receipt_id=receipts["stop"].id,
        stop_receipt_hash=receipts["stop"].payload_sha256,
        rejection_receipt_id=receipts["rejection"].id,
        rejection_receipt_hash=receipts["rejection"].payload_sha256,
        rejection=expected["rejection"],
    )


def settle_numeric_rejection(session, fence, admission_id, *, storage_root):
    """Re-read first, charge the entire original hold once. Flush-only."""
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from backend.app.research_broker_services import settle_budget

    proof = read_numeric_rejection(
        session, fence, admission_id, storage_root=storage_root
    )
    reservation = session.get(
        ResearchBudgetReservation, proof.admission.reservation_id, with_for_update=True
    )
    if reservation is None:
        raise ProcessError("numeric_rejection_reservation")
    _scope(reservation, fence)
    amount = BudgetVector.model_validate(reservation.amounts)
    if reservation.purpose != "process_unit" or not amount.covers(
        BudgetVector(process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400)
    ):
        raise ProcessError("numeric_rejection_reservation")
    previous = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == reservation.id
        )
    )
    if previous is not None:
        _scope(previous, fence)
        if (
            reservation.state != "settled"
            or previous.charge != reservation.amounts
            or previous.outcome != "conservative_unknown"
        ):
            raise ProcessError("numeric_rejection_reservation")
    elif reservation.state not in {"reserved", "uncertain"}:
        raise ProcessError("numeric_rejection_reservation")
    settle_budget(
        session, fence, reservation.id, charge=amount, outcome="conservative_unknown"
    )
    return proof
