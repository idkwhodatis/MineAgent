"""Explicit no-IPC persistence/read boundary; not an execution issuer.

No API/dispatcher invokes this module. Admission records describe obligations,
not permission to launch. Only a separately accepted protected supervisor can
supply journals; caller DTOs are never accepted as extraction authorization.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.analysis_process_journal import (
    AnalysisInputBinding,
    AnalysisIntent,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    decode_row,
    encode_row,
)


def require_analysis_capability(session):
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if (
        len(revisions) != 1
        or getattr(capabilities_for(revisions[0]), "direct_analysis_schema", None)
        != "mineagent.direct-analysis-restore-closure.v1"
    ):
        raise ProcessError("analysis_process_schema_unsupported")


def as_analysis_admission(row):
    value = decode_row({name: getattr(row, name) for name in ROW_FIELDS})
    if (
        value.role != "native_evaluator"
        or value.profile != "mineagent.qlib-direct-analysis.v1"
        or value.ipc.ipc_policy != "none"
    ):
        raise ProcessError("analysis_process_profile")
    return value


def _scope(value, fence):
    if any(
        getattr(value, name) != getattr(fence, name)
        for name in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        raise ProcessError("analysis_process_scope")


def persist_analysis_intent(session, fence, intent):
    """Flush a single immutable obligation; does not mint launch authority.

    Input/source graph authenticity is the upstream persisted admission service's
    responsibility. It cannot be substituted once this intent is committed.
    """
    from backend.app.research_broker_services import require_reservation
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.research_process_services import _receipt
    from packages.research_runtime.budget_contracts import BudgetVector

    require_neutral_transaction(session, fence, resource_scope="processes")
    require_analysis_capability(session)
    intent = AnalysisIntent.model_validate(intent)
    admission = intent.admission
    _scope(admission, fence)
    # Deliberately conservative existing process reservation floor includes
    # retained opaque output plus bounded control receipts, not only tmpfs.
    reservation = require_reservation(
        session,
        fence,
        admission.reservation_id,
        purpose="process_unit",
        minimum=BudgetVector(
            process_slots=1,
            evaluator_executions=1,
            wall_milliseconds=60000,
            storage_bytes=67_174_400,
        ),
    )
    if reservation.logical_key != "direct-analysis:" + str(intent.inputs.analysis_id):
        raise ProcessError("analysis_process_reservation")
    previous = session.scalar(
        select(ResearchProcessAdmission).where(
            ResearchProcessAdmission.owner_id == admission.owner_id,
            ResearchProcessAdmission.run_id == admission.run_id,
            ResearchProcessAdmission.attempt_id == admission.attempt_id,
            ResearchProcessAdmission.generation == admission.generation,
            ResearchProcessAdmission.role == admission.role,
            ResearchProcessAdmission.logical_operation_id
            == admission.logical_operation_id,
            ResearchProcessAdmission.launch_ordinal == admission.launch_ordinal,
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


def _persisted(session, admission_id):
    row = session.get(ResearchProcessAdmission, admission_id, with_for_update=True)
    if row is None:
        raise ProcessError("analysis_process_missing")
    admission = as_analysis_admission(row)
    state = session.get(ResearchProcessState, admission_id, with_for_update=True)
    if state is None:
        raise ProcessError("analysis_process_missing")
    receipts = session.scalars(
        select(ResearchProcessReceipt).where(
            ResearchProcessReceipt.admission_id == admission_id
        )
    ).all()
    by_kind = {}
    for receipt in receipts:
        _scope(receipt, admission)
        if receipt.kind in by_kind or receipt.payload_sha256 != digest(receipt.payload):
            raise ProcessError("analysis_process_receipt")
        by_kind[receipt.kind] = receipt
    _scope(state, admission)
    return admission, state, by_kind


def record_analysis_stopped(
    session, fence, admission_id, *, storage_root, with_output=False
):
    """Ingest protected physical proof under the original current fence.

    Post-lease containment recovery must use a future separate containment-only
    supervisor route; this does not relax current-fence result authority.
    """
    from backend.app.models import utcnow
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.research_process_services import _receipt
    from runtimes.research_supervisor.analysis_journal import (
        read_analysis_journal,
        read_opaque_output,
    )

    require_neutral_transaction(session, fence, resource_scope="processes")
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    launch, identity, stop, extraction = read_analysis_journal(
        storage_root, admission, require_output=with_output
    )
    intent = receipts.get("launch_intent")
    if intent is None or intent.payload != launch.intent.model_dump(mode="json"):
        raise ProcessError("analysis_process_intent")
    if state.status not in {"launch_intended", "running", "uncertain", "stopped"}:
        raise ProcessError("analysis_process_state")
    if extraction is not None and not stop.deadline_bound_met:
        raise ProcessError("analysis_process_output_overdue")
    if extraction is not None:
        read_opaque_output(storage_root, admission, extraction)
    _receipt(session, admission, "launch_identity", identity.model_dump(mode="json"))
    _receipt(session, admission, "stop", stop.model_dump(mode="json"))
    if extraction is not None:
        _receipt(session, admission, "extraction", extraction.model_dump(mode="json"))
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()


@dataclass(frozen=True)
class AnalysisOutputReceipt:
    admission: ProcessDeclarationV2
    inputs: AnalysisInputBinding
    launch_identity_receipt_id: UUID
    launch_identity_receipt_hash: str
    stop_receipt_id: UUID
    stop_receipt_hash: str
    extraction_receipt_id: UUID
    extraction_receipt_hash: str
    basename: str
    sha256: str
    size_bytes: int


def _resolve(session, fence, admission_id, storage_root):
    from backend.app.research_lifecycle import require_neutral_transaction
    from runtimes.research_supervisor.analysis_journal import read_analysis_journal

    require_neutral_transaction(session, fence)
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    if state.status != "stopped" or set(receipts) != {
        "launch_intent",
        "launch_identity",
        "stop",
        "extraction",
    }:
        raise ProcessError("analysis_process_output_uncommitted")
    launch, identity, stop, extraction = read_analysis_journal(
        storage_root, admission, require_output=True
    )
    if not stop.deadline_bound_met:
        raise ProcessError("analysis_process_output_overdue")
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "stop": stop,
        "extraction": extraction,
    }
    if any(
        receipts[kind].payload != value.model_dump(mode="json")
        for kind, value in expected.items()
    ):
        raise ProcessError("analysis_process_receipt")
    receipt = AnalysisOutputReceipt(
        admission,
        launch.intent.inputs,
        receipts["launch_identity"].id,
        receipts["launch_identity"].payload_sha256,
        receipts["stop"].id,
        receipts["stop"].payload_sha256,
        receipts["extraction"].id,
        receipts["extraction"].payload_sha256,
        extraction.basename,
        extraction.sha256,
        extraction.size_bytes,
    )
    return receipt, extraction


def resolve_analysis_output(session, fence, admission_id, *, storage_root):
    """Return exact current DB/physical binding, never a reusable capability."""
    from runtimes.research_supervisor.analysis_journal import read_opaque_output

    receipt, extraction = _resolve(session, fence, admission_id, storage_root)
    read_opaque_output(storage_root, receipt.admission, extraction)
    return receipt


def read_analysis_output(session, fence, admission_id, *, storage_root):
    """Freshly resolve then read bounded opaque bytes via pinned directory FDs."""
    from runtimes.research_supervisor.analysis_journal import read_opaque_output

    receipt, extraction = _resolve(session, fence, admission_id, storage_root)
    return receipt, read_opaque_output(storage_root, receipt.admission, extraction)


def analysis_stop_committed(session, row):
    """Terminal barrier: all exact immutable analysis receipts, never v1 fallback."""
    from packages.research_runtime.analysis_process_journal import (
        AnalysisIdentity,
        AnalysisLaunch,
        AnalysisStop,
        validate_analysis_journal,
    )
    from packages.research_runtime.process_contracts import canonical

    try:
        admission, state, receipts = _persisted(session, row.id)
        if state.status != "stopped" or not {
            "launch_intent",
            "launch_identity",
            "stop",
        } <= set(receipts):
            return False
        intent = AnalysisIntent.model_validate_json(
            canonical(receipts["launch_intent"].payload)
        )
        identity = AnalysisIdentity.model_validate_json(
            canonical(receipts["launch_identity"].payload)
        )
        stop = AnalysisStop.model_validate_json(canonical(receipts["stop"].payload))
        launch = AnalysisLaunch(
            schema_version="mineagent.qlib-analysis-process-launch.v1",
            intent=intent,
            runtime_name="mineagent-analysis-" + str(admission.id),
            root_device=identity.root_device,
            root_inode=identity.root_inode,
        )
        validate_analysis_journal(launch, identity, stop)
        return (
            identity.launch_sha256 == digest(launch.model_dump(mode="json"))
            and intent.admission == admission
            and identity.admission_sha256 == digest(admission.model_dump(mode="json"))
            and identity.image_id == admission.image_id
            and stop.identity_sha256 == digest(identity.model_dump(mode="json"))
            and stop.launch_sha256 == identity.launch_sha256
            and stop.container_id == identity.container_id
        )
    except (ValueError, ProcessError):
        return False


@dataclass(frozen=True)
class StoppedAnalysisReceipt(AnalysisOutputReceipt):
    result_bytes: bytes

    @property
    def analysis_id(self):
        return self.inputs.analysis_id

    @property
    def result_sha256(self):
        return self.sha256


def read_stopped_analysis(session, fence, admission_id, settings):
    """Fresh graph + protected result + whole-unit stop, never a detached grant."""
    import hashlib
    from dataclasses import fields

    from backend.app.factor_analysis_services import (
        require_analysis_plan,
        resolve_direct_analysis_input,
    )
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.qlib_adapter.direct_wire import read_result, write_input

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    require_analysis_capability(session)
    receipt, raw = read_analysis_output(
        session, fence, admission_id, storage_root=settings.storage_root
    )
    _completed_accounting(session, fence, receipt.admission)
    pins = require_analysis_plan(session, fence, receipt.inputs.analysis_id)
    if (
        receipt.inputs.snapshot_id,
        receipt.inputs.label_source_id,
        receipt.admission.image_id,
        receipt.inputs.protocol_sha256,
        receipt.inputs.analysis_input_sha256,
        receipt.inputs.analysis_input_size_bytes,
    ) != (
        pins.snapshot_id,
        pins.label_source_id,
        pins.qlib_image_id,
        pins.protocol_sha256,
        pins.analysis_input_sha256,
        pins.analysis_input_size_bytes,
    ):
        raise ProcessError("analysis_process_plan_drift")
    inputs = resolve_direct_analysis_input(
        session, fence, receipt.inputs.analysis_id, settings
    )
    projected = write_input(inputs)
    if (
        inputs.protocol.snapshot_id,
        inputs.protocol.sha256,
        hashlib.sha256(projected).hexdigest(),
        len(projected),
    ) != (
        receipt.inputs.snapshot_id,
        receipt.inputs.protocol_sha256,
        receipt.inputs.analysis_input_sha256,
        receipt.inputs.analysis_input_size_bytes,
    ):
        raise ProcessError("analysis_process_retained_input_drift")
    result = read_result(raw, inputs)
    return StoppedAnalysisReceipt(
        **{f.name: getattr(receipt, f.name) for f in fields(AnalysisOutputReceipt)},
        result_bytes=raw,
    ), result


def _completed_accounting(session, fence, admission):
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    reservation = session.get(ResearchBudgetReservation, admission.reservation_id)
    settlement = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == admission.reservation_id
        )
    )
    if reservation is None:
        raise ProcessError("analysis_process_reservation")
    _scope(reservation, fence)
    if (
        reservation.purpose != "process_unit"
        or reservation.logical_key
        != "direct-analysis:" + str(admission.logical_operation_id)
        or not BudgetVector.model_validate(reservation.amounts).covers(
            BudgetVector(
                process_slots=1,
                evaluator_executions=1,
                wall_milliseconds=60000,
                storage_bytes=67_174_400,
            )
        )
    ):
        raise ProcessError("analysis_process_reservation")
    if reservation.state in {"reserved", "uncertain"} and settlement is None:
        return
    if reservation.state == "settled" and settlement is not None:
        _scope(settlement, fence)
        if (
            settlement.charge == reservation.amounts
            and settlement.outcome == "conservative_unknown"
        ):
            return
    raise ProcessError("analysis_process_reservation")
