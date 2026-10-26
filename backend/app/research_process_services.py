"""Flush-only durable admission and append-only containment evidence."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import (
    ContainmentSummary,
    ProcessAdmission,
    ProcessError,
    canonical,
    digest,
)
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    encode_row,
    require_persistable,
)


def as_admission(row):
    return require_persistable({name: getattr(row, name) for name in ROW_FIELDS})


def admit_unit(
    session,
    fence,
    *,
    role,
    logical_operation_id,
    launch_ordinal,
    profile,
    input_sha256,
    reservation_id,
    image_id,
    ticket_sha256,
):
    from backend.app.research_broker_services import require_reservation
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.research_runtime.process_profiles import (
        profile_for,
        require_configured_image,
    )

    require_neutral_transaction(session, fence, resource_scope="processes")
    require_configured_image(image_id)
    admission = ProcessAdmission(
        uuid4(),
        fence.owner_id,
        fence.run_id,
        fence.attempt_id,
        fence.generation,
        role,
        logical_operation_id,
        launch_ordinal,
        profile,
        input_sha256,
        reservation_id,
        image_id,
        ticket_sha256,
    )
    require_reservation(
        session,
        fence,
        reservation_id,
        purpose="process_unit",
        minimum=profile_for(profile, role).reservation,
    )
    previous = session.scalar(
        select(ResearchProcessAdmission).where(
            ResearchProcessAdmission.owner_id == fence.owner_id,
            ResearchProcessAdmission.run_id == fence.run_id,
            ResearchProcessAdmission.attempt_id == fence.attempt_id,
            ResearchProcessAdmission.generation == fence.generation,
            ResearchProcessAdmission.role == role,
            ResearchProcessAdmission.logical_operation_id == logical_operation_id,
            ResearchProcessAdmission.launch_ordinal == launch_ordinal,
        )
    )
    if previous is not None:
        expected = admission.public()
        expected["id"] = str(previous.id)
        if as_admission(previous).public() != expected:
            raise ProcessError("research_process_identity")
        # A caller must reconcile the persisted unit; returning existing admission
        # is not authorization to repeat a physical launch.
        raise ProcessError("research_process_already_admitted")
    row = ResearchProcessAdmission(**encode_row(admission))
    session.add(row)
    session.flush()
    session.add(
        ResearchProcessState(
            admission_id=row.id,
            owner_id=row.owner_id,
            run_id=row.run_id,
            attempt_id=row.attempt_id,
            generation=row.generation,
            status="admitted",
        )
    )
    session.flush()
    return admission


def _owned(session, admission):
    row = session.scalar(
        select(ResearchProcessAdmission)
        .where(ResearchProcessAdmission.id == admission.id)
        .with_for_update()
    )
    if row is None or as_admission(row) != admission:
        raise ProcessError("research_process_identity")
    state = session.get(ResearchProcessState, admission.id, with_for_update=True)
    if state is None:
        raise ProcessError("research_process_uncertain")
    return state


def _receipt(session, admission, kind, payload):
    if len(canonical(payload)) > 16384:
        raise ProcessError("research_process_receipt")
    previous = session.scalar(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == admission.id,
            ResearchProcessReceipt.kind == kind,
        )
    )
    if previous is not None:
        if previous.payload != payload or previous.payload_sha256 != digest(payload):
            raise ProcessError("research_process_receipt")
        return previous
    row = ResearchProcessReceipt(
        id=uuid4(),
        owner_id=admission.owner_id,
        run_id=admission.run_id,
        attempt_id=admission.attempt_id,
        generation=admission.generation,
        admission_id=admission.id,
        kind=kind,
        payload=payload,
        payload_sha256=digest(payload),
    )
    session.add(row)
    session.flush()
    return row


def record_identity(session, fence, admission, identity, *, storage_root):
    from backend.app.research_lifecycle import require_neutral_transaction
    from runtimes.research_supervisor.supervisor import (
        load_launch,
        operation_path,
        read_record,
        validate_identity,
    )

    require_neutral_transaction(session, fence, resource_scope="processes")
    if (
        admission.owner_id,
        admission.run_id,
        admission.attempt_id,
        admission.generation,
    ) != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation):
        raise ProcessError("research_process_identity")
    state = _owned(session, admission)
    path = operation_path(storage_root, admission)
    launch = load_launch(path, admission, storage_root)
    validate_identity(identity, launch)
    _require_journal_intent(session, admission, launch)
    if read_record(path / "control" / "identity.json") != identity:
        raise ProcessError("research_process_receipt")
    if state.status != "launch_intended":
        raise ProcessError("research_process_uncertain")
    _receipt(session, admission, "launch_identity", identity)
    state.status, state.updated_at = "running", utcnow()
    session.flush()


def mark_uncertain(session, fence, admission):
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence, resource_scope="processes")
    if (
        admission.owner_id,
        admission.run_id,
        admission.attempt_id,
        admission.generation,
    ) != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation):
        raise ProcessError("research_process_identity")
    state = _owned(session, admission)
    if state.status != "stopped":
        state.status, state.updated_at = "uncertain", utcnow()
        session.flush()


def record_stopped(session, admission, *, storage_root):
    """Containment-only recovery may persist proof after lease loss.

    Does not update attempts, publish results, settle/refund budgets or authorize
    work. The caller owns a fresh transaction; exact owner->Run->Attempt order.
    """
    from backend.app.services import advisory_lock
    from runtimes.research_supervisor.supervisor import (
        load_launch,
        operation_path,
        read_record,
        validate_stopped_journal,
    )

    advisory_lock(session, f"submit:{admission.owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.id == admission.run_id,
            ResearchRun.owner_id == admission.owner_id,
        )
        .with_for_update()
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.id == admission.attempt_id,
            ExecutionAttempt.run_id == admission.run_id,
            ExecutionAttempt.generation == admission.generation,
        )
        .with_for_update()
    )
    if run is None or attempt is None:
        raise ProcessError("research_process_identity")
    state = _owned(session, admission)
    path = operation_path(storage_root, admission)
    _require_journal_intent(session, admission, load_launch(path, admission))
    stop = validate_stopped_journal(path, admission)
    identity = read_record(path / "control" / "identity.json")
    _receipt(session, admission, "launch_identity", identity)
    _receipt(session, admission, "stop", stop)
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()
    return stop


def reconcile_attempt_units(session, run_id, attempt_id, generation):
    rows = session.scalars(
        select(ResearchProcessAdmission).where(
            ResearchProcessAdmission.run_id == run_id,
            ResearchProcessAdmission.attempt_id == attempt_id,
            ResearchProcessAdmission.generation == generation,
        )
    ).all()
    stopped = 0
    for row in rows:
        if row.schema_version == "mineagent.process-admission.v2":
            from backend.app.numeric_process_services import numeric_stop_committed

            stopped += int(numeric_stop_committed(session, row))
            continue
        state = session.get(ResearchProcessState, row.id)
        stop = session.scalar(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == row.id,
                ResearchProcessReceipt.kind == "stop",
            )
        )
        identity = session.scalar(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == row.id,
                ResearchProcessReceipt.kind == "launch_identity",
            )
        )
        if (
            state is not None
            and state.status == "stopped"
            and stop is not None
            and identity is not None
            and stop.payload_sha256 == digest(stop.payload)
            and identity.payload_sha256 == digest(identity.payload)
            and identity.payload.get("admission_sha256")
            == digest(as_admission(row).public())
            and stop.payload.get("confirmed_absent") is True
            and stop.payload.get("container_id") == identity.payload.get("container_id")
            and stop.payload.get("launch_sha256")
            == identity.payload.get("launch_sha256")
        ):
            stopped += 1
    return ContainmentSummary(len(rows), stopped)


def assert_attempt_units_stopped(session, run_id, attempt_id, generation):
    if not reconcile_attempt_units(session, run_id, attempt_id, generation).confirmed:
        raise ProcessError("research_process_uncertain")


def commit_launch_intent(session, fence, admission_id):
    """Flush-only one-use intent; runtime authority is minted only after commit."""
    from datetime import timedelta

    from backend.app.research_broker_services import require_reservation
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.research_runtime.process_profiles import (
        profile_for,
        require_configured_image,
    )

    require_neutral_transaction(session, fence, resource_scope="processes")
    row = session.get(ResearchProcessAdmission, admission_id)
    if row is None:
        raise ProcessError("research_process_uncommitted")
    admission = as_admission(row)
    if (
        admission.owner_id,
        admission.run_id,
        admission.attempt_id,
        admission.generation,
    ) != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation):
        raise ProcessError("research_process_identity")
    require_configured_image(admission.image_id)
    profile = profile_for(admission.profile, admission.role)
    reservation = require_reservation(
        session,
        fence,
        admission.reservation_id,
        purpose="process_unit",
        minimum=profile.reservation,
    )
    if reservation.state != "reserved":
        raise ProcessError("research_process_uncertain")
    state = _owned(session, admission)
    if (
        state.status != "admitted"
        or session.scalar(
            select(ResearchProcessReceipt.id).where(
                ResearchProcessReceipt.admission_id == admission.id,
                ResearchProcessReceipt.kind == "launch_intent",
            )
        )
        is not None
    ):
        raise ProcessError("research_process_already_admitted")
    now = utcnow()
    intent = {
        "schema_version": "mineagent.research-process-intent.v1",
        "intent_id": str(uuid4()),
        "admission_sha256": digest(admission.public()),
        "issued_at": now.isoformat(),
        "not_after": (now + timedelta(seconds=profile.wall_seconds)).isoformat(),
    }
    _receipt(session, admission, "launch_intent", intent)
    state.status, state.updated_at = "launch_intended", now
    session.flush()
    return admission, intent


def require_running_peer(session, fence, authorization, logical_operation_id):
    """A live kernel peer is insufficient without committed running provenance."""
    from backend.app.research_lifecycle import require_neutral_transaction
    from runtimes.research_supervisor.ipc import require_authenticated_peer

    require_neutral_transaction(session, fence)
    peer = require_authenticated_peer(
        authorization, fence, logical_operation_id=logical_operation_id
    )
    row = session.get(ResearchProcessAdmission, peer.admission_id)
    state = session.get(ResearchProcessState, peer.admission_id)
    receipt = session.scalar(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == peer.admission_id,
            ResearchProcessReceipt.kind == "launch_identity",
        )
    )
    intent = session.scalar(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == peer.admission_id,
            ResearchProcessReceipt.kind == "launch_intent",
        )
    )
    if (
        row is None
        or as_admission(row) != peer.admission
        or state is None
        or state.status != "running"
        or receipt is None
        or intent is None
        or receipt.payload_sha256 != digest(receipt.payload)
        or receipt.payload.get("admission_sha256") != digest(peer.admission.public())
        or any(receipt.payload.get(key) != value for key, value in peer.leader.items())
    ):
        raise ProcessError("research_process_peer")
    return row


def check_launch_intent(session, fence, admission, intent):
    from backend.app.research_broker_services import require_reservation
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.research_runtime.process_profiles import (
        profile_for,
    )

    require_neutral_transaction(session, fence, resource_scope="processes")
    row = session.get(ResearchProcessAdmission, admission.id)
    state = session.get(ResearchProcessState, admission.id)
    receipt = session.scalar(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == admission.id,
            ResearchProcessReceipt.kind == "launch_intent",
        )
    )
    reservation = require_reservation(
        session,
        fence,
        admission.reservation_id,
        purpose="process_unit",
        minimum=profile_for(admission.profile, admission.role).reservation,
    )
    if (
        row is None
        or as_admission(row) != admission
        or state is None
        or state.status != "launch_intended"
        or receipt is None
        or receipt.payload != intent
        or receipt.payload_sha256 != digest(intent)
        or reservation.state != "reserved"
    ):
        raise ProcessError("research_process_launch_authority")


def _require_journal_intent(session, admission, launch):
    from packages.research_runtime.process_contracts import validate_launch_intent

    receipt = session.scalar(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == admission.id,
            ResearchProcessReceipt.kind == "launch_intent",
        )
    )
    if (
        receipt is None
        or receipt.payload != launch["intent"]
        or receipt.payload_sha256 != digest(launch["intent"])
    ):
        raise ProcessError("research_process_launch_authority")
    validate_launch_intent(receipt.payload, admission)
