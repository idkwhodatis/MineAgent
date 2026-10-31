"""Exact027 shared SDK/transfer peak accounting under the existing quota lock.

Membership/accounting reads only, not physical receipt verification or launch
permission. Installed writers/SQL and retained-file validators remain mandatory.
Unknown, orphan and incompletely settled units retain their full peak.
"""

from sqlalchemy import select, text

from backend.app.oe_structured_checkpoint_models import OEStructuredCheckpointUpload
from backend.app.oe_structured_operational_models import (
    OEStructuredEpoch,
    OEStructuredProviderReceipt,
    OEStructuredProviderTransport,
)
from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
    ResearchModelOperation,
    ResearchModelTransport,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.rdagent_provider.codec import PROTOCOLS as RD_PROTOCOLS
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical, digest
from runtimes.openevolve_structured_host.process_journal import ControllerStop

REVISION = "028_oe_structured_search"
OE_PROTOCOL = "mineagent.oe-structured-provider-chat.v1"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
LIMIT = 10000


def _active(session):
    return session.scalars(text("SELECT version_num FROM alembic_version")).all() in (
        [REVISION],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    )


def _same(reference, *rows):
    return all(
        row is not None and all(getattr(row, k) == getattr(reference, k) for k in SCOPE)
        for row in rows
    )


def _amount(value):
    result = BudgetVector.model_validate(value)
    if value != result.model_dump():
        raise ValueError("Exact eight-dimensional quota vector required")
    return result


def _settled(reservation, settlement):
    if settlement is None:
        return False
    try:
        charge = _amount(settlement.charge)
    except ValueError:
        return False
    return (
        settlement is not None
        and _same(reservation, settlement)
        and settlement.reservation_id == reservation.id
        and reservation.state == "settled"
        and settlement.outcome == "conservative_unknown"
        and charge == _amount(reservation.amounts)
    )


def _bounded(rows):
    if len(rows) > LIMIT:
        raise ValueError("Structured quota inventory bound")
    return rows


def _receipts(session, model, column, identity, reference, kind):
    rows = session.scalars(select(model).where(column == identity).limit(4)).all()
    if len(rows) != 3 or {r.kind for r in rows} != {"intent", "started", "stopped"}:
        return False
    return all(
        _same(reference, r)
        and r.receipt_object_kind == kind
        and 0 < r.size_bytes <= 16384
        for r in rows
    )


def sdk_process_holds(session):
    """One reservation-first inventory for RD AND normal OE; never sum both scans."""
    if not _active(session):
        return ()
    rows = _bounded(
        session.execute(
            select(
                ResearchBudgetReservation,
                ResearchModelTransport,
                ResearchBudgetSettlement,
                ResearchModelOperation,
                OEStructuredProviderTransport,
            )
            .select_from(ResearchBudgetReservation)
            .outerjoin(
                ResearchModelTransport,
                ResearchModelTransport.reservation_id == ResearchBudgetReservation.id,
            )
            .outerjoin(
                ResearchBudgetSettlement,
                ResearchBudgetSettlement.reservation_id == ResearchBudgetReservation.id,
            )
            .outerjoin(
                ResearchModelOperation,
                ResearchModelOperation.id == ResearchModelTransport.operation_id,
            )
            .outerjoin(
                OEStructuredProviderTransport,
                OEStructuredProviderTransport.transport_id == ResearchModelTransport.id,
            )
            .where(
                ResearchBudgetReservation.purpose == "model_transport",
                ResearchBudgetReservation.amounts["process_slots"].as_integer() > 0,
            )
            .limit(LIMIT + 1)
        ).all()
    )
    holds = []
    seen = set()
    for reservation, transport, settlement, operation, member in rows:
        if reservation.id in seen:
            raise ValueError("SDK reservation has multiple consumers")
        seen.add(reservation.id)
        amount = _amount(reservation.amounts)
        complete = False
        if (
            _settled(reservation, settlement)
            and _same(reservation, transport, operation)
            and transport.operation_id == operation.id
            and transport.reservation_id == reservation.id
            and transport.state in {"completed", "failed", "unknown"}
        ):
            if operation.protocol in RD_PROTOCOLS and member is not None:
                raise ValueError("SDK reservation has cross-engine consumers")
            if operation.protocol in RD_PROTOCOLS and member is None:
                complete = _receipts(
                    session,
                    RDProviderTransportReceipt,
                    RDProviderTransportReceipt.transport_id,
                    transport.id,
                    reservation,
                    "rd_provider_process_receipt",
                )
            elif (
                operation.protocol == OE_PROTOCOL
                and _same(reservation, member)
                and member.transport_id == transport.id
                and member.operation_id == operation.id
                and member.request_sha256 == operation.request_sha256
                and transport.ordinal == 1
                and reservation.logical_key
                == "oe-structured-provider:" + str(member.iteration_intent_id)
            ):
                complete = _receipts(
                    session,
                    OEStructuredProviderReceipt,
                    OEStructuredProviderReceipt.membership_id,
                    member.id,
                    reservation,
                    "oe_structured_provider_receipt",
                )
        if not complete:
            holds.append((reservation.owner_id, amount.storage_bytes, 8))
    return tuple(holds)


def _controller_settled(session, upload):
    epoch = session.get(OEStructuredEpoch, upload.epoch_id)
    if not _same(upload, epoch):
        return False
    admission = session.get(ResearchProcessAdmission, epoch.controller_admission_id)
    state = session.get(ResearchProcessState, epoch.controller_admission_id)
    if (
        not _same(upload, admission, state)
        or state.status != "stopped"
        or admission.role != "openevolve_orchestrator"
        or admission.profile != "mineagent.oe-structured-orchestrator.v1"
    ):
        return False
    reservation = session.get(ResearchBudgetReservation, admission.reservation_id)
    settlements = session.scalars(
        select(ResearchBudgetSettlement)
        .filter_by(reservation_id=admission.reservation_id)
        .limit(2)
    ).all()
    if (
        not _same(upload, reservation)
        or reservation.purpose != "process_unit"
        or reservation.logical_key != "oe-structured-controller:" + str(epoch.id)
        or len(settlements) != 1
        or not _settled(reservation, settlements[0])
    ):
        return False
    receipts = session.scalars(
        select(ResearchProcessReceipt)
        .filter_by(admission_id=admission.id, kind="stop")
        .limit(2)
    ).all()
    if len(receipts) != 1 or not _same(upload, receipts[0]):
        return False
    stop = receipts[0]
    ControllerStop.model_validate_json(canonical(stop.payload))
    if stop.payload_sha256 != digest(stop.payload):
        raise ValueError("Controller stop commitment differs")
    return True


def checkpoint_upload_holds(session):
    if not _active(session):
        return ()
    rows = _bounded(
        session.execute(
            select(
                ResearchBudgetReservation,
                OEStructuredCheckpointUpload,
                ResearchBudgetSettlement,
            )
            .select_from(ResearchBudgetReservation)
            .outerjoin(
                OEStructuredCheckpointUpload,
                OEStructuredCheckpointUpload.reservation_id
                == ResearchBudgetReservation.id,
            )
            .outerjoin(
                ResearchBudgetSettlement,
                ResearchBudgetSettlement.reservation_id == ResearchBudgetReservation.id,
            )
            .where(
                ResearchBudgetReservation.purpose == "object_storage",
                ResearchBudgetReservation.logical_key.startswith(
                    "oe-structured-upload:"
                ),
            )
            .limit(LIMIT + 1)
        ).all()
    )
    holds = []
    seen = set()
    for reservation, upload, settlement in rows:
        if reservation.id in seen:
            raise ValueError("Upload reservation has multiple consumers")
        seen.add(reservation.id)
        amount = _amount(reservation.amounts)
        if upload is None:
            holds.append((reservation.owner_id, amount.storage_bytes, 400))
            continue
        if (
            not _same(reservation, upload)
            or upload.reservation_id != reservation.id
            or reservation.logical_key
            != "oe-structured-upload:" + str(upload.intent_id)
            or not 4 <= upload.total_bytes <= 8388608
            or not 4 <= upload.file_count <= 11
            or not upload.file_count <= upload.chunk_count <= 88
        ):
            raise ValueError("Checkpoint upload quota binding differs")
        expected = BudgetVector(
            storage_bytes=2 * upload.total_bytes
            + 2 * 1048576
            + 2 * 4096 * (upload.file_count + upload.chunk_count + 1)
        )
        if amount != expected:
            raise ValueError("Checkpoint transfer peak vector differs")
        if _settled(reservation, settlement) and _controller_settled(session, upload):
            continue
        holds.append(
            (
                reservation.owner_id,
                amount.storage_bytes,
                4 * (upload.chunk_count + upload.file_count + 1),
            )
        )
    return tuple(holds)
