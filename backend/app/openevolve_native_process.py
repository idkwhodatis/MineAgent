"""024 fixed native evaluator obligations and one-shot launch/stop persistence.

The host resolves the retained full input manifest and saved controller runtime
binding. No caller descriptor grants a launch. No model/controller liveness is
required to preserve truthful stopped evidence under the original current fence.
"""

import hashlib
import os

from sqlalchemy import select

from backend.app.models import utcnow
from backend.app.openevolve_models import SearchEvaluation, StrategySearchSpec
from backend.app.openevolve_operational_models import (
    SearchControllerLaunch,
    SearchEvaluationInput,
)
from backend.app.openevolve_services import _current_search, _owned_row, _scope
from backend.app.research_broker_services import require_reservation, settle_budget
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_services import read_research_object
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.research_process_services import _receipt
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec
from packages.research_runtime.broker_contracts import digest as broker_digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    decode_row,
    encode_row,
)
from runtimes.openevolve_evaluator.contracts import PROFILE, NativeInputManifest
from runtimes.openevolve_evaluator.journal import (
    NativeIdentity,
    NativeIntent,
    NativeLaunch,
)
from runtimes.openevolve_evaluator.output_reader import read_native_journal
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import _directory, operation_path

FLOOR = BudgetVector(
    process_slots=1,
    evaluator_executions=1,
    wall_milliseconds=90000,
    storage_bytes=268500992,
)


def _exact_scope(row, fence):
    if row is None or any(getattr(row, k) != v for k, v in _scope(fence).items()):
        raise ProcessError("oe_native_scope")


def resolve_native_input(session, fence, intent, settings):
    require_neutral_transaction(session, fence)
    _current_search(session, fence)
    intent = NativeIntent.model_validate(intent)
    admission = intent.admission
    _exact_scope(admission, fence)
    evaluation = _owned_row(
        session, SearchEvaluation, admission.logical_operation_id, fence
    )
    binding = session.scalar(
        select(SearchEvaluationInput).filter_by(evaluation_id=evaluation.id)
    )
    _exact_scope(binding, fence)
    launch = session.scalar(
        select(SearchControllerLaunch).filter_by(epoch_id=evaluation.epoch_id)
    )
    _exact_scope(launch, fence)
    from backend.app.openevolve_controller_services import typed_bootstrap

    bootstrap, _raw_bootstrap = typed_bootstrap(launch)
    specrow = session.get(StrategySearchSpec, fence.run_id)
    if specrow is None or specrow.owner_id != fence.owner_id:
        raise ProcessError("oe_native_spec")
    spec = SearchSpec.model_validate_json(canonical(specrow.spec))
    if (
        bootstrap.spec != spec
        or hashlib.sha256(launch.bootstrap_canonical.encode("utf-8")).hexdigest()
        != launch.bootstrap_sha256
    ):
        raise ProcessError("oe_native_runtime_binding")
    raw = read_research_object(
        session,
        fence,
        binding.manifest_object_id,
        binding.manifest_kind,
        binding.manifest_sha256,
        settings,
    )
    manifest = NativeInputManifest.model_validate_json(raw)
    intent.inputs.require_manifest(manifest)
    window = next(
        (w for w in spec.execution.windows if w.id == evaluation.window_id), None
    )
    if (
        window is None
        or canonical(manifest.model_dump(mode="json")) != raw
        or (
            binding.epoch_id,
            binding.manifest_object_id,
            binding.manifest_sha256,
            binding.manifest_size_bytes,
            evaluation.process_admission_id,
            evaluation.reservation_id,
            evaluation.physical_ordinal,
            evaluation.source_sha256,
            evaluation.execution_sha256,
            evaluation.input_manifest_sha256,
            admission.image_id,
            manifest.window_start,
            manifest.window_end,
        )
        != (
            evaluation.epoch_id,
            intent.inputs.manifest_object_id,
            intent.inputs.manifest_sha256,
            len(raw),
            admission.id,
            admission.reservation_id,
            admission.launch_ordinal,
            manifest.source_sha256,
            broker_digest(spec.execution),
            manifest.native_artifacts_sha256,
            bootstrap.native_runtime.protected_image_id,
            window.start,
            window.end,
        )
    ):
        raise ProcessError("oe_native_exact_input_binding")
    return evaluation, manifest, bootstrap.native_runtime


def _require_held(session, fence, admission):
    reservation = require_reservation(
        session, fence, admission.reservation_id, purpose="process_unit", minimum=FLOOR
    )
    if (
        reservation.logical_key
        != f"oe-native:{admission.logical_operation_id}:{admission.launch_ordinal}"
    ):
        raise ProcessError("oe_native_reservation_identity")
    return reservation


def persist_native_intent(session, fence, intent, settings):
    require_neutral_transaction(session, fence, resource_scope="processes")
    intent = NativeIntent.model_validate(intent)
    admission = intent.admission
    resolve_native_input(session, fence, intent, settings)
    _require_held(session, fence, admission)
    if (
        session.get(ResearchProcessAdmission, admission.id) is not None
        or session.scalar(
            select(ResearchProcessAdmission).filter_by(
                **_scope(fence),
                role="native_evaluator",
                logical_operation_id=admission.logical_operation_id,
                launch_ordinal=admission.launch_ordinal,
            )
        )
        is not None
    ):
        raise ProcessError("oe_native_already_admitted")
    session.add(ResearchProcessAdmission(**encode_row(admission)))
    session.flush()
    session.add(
        ResearchProcessState(
            admission_id=admission.id, **_scope(fence), status="launch_intended"
        )
    )
    _receipt(session, admission, "launch_intent", intent.model_dump(mode="json"))
    session.flush()
    return admission


def persisted(session, fence, admission_id):
    require_neutral_transaction(session, fence, resource_scope="processes")
    row = session.get(ResearchProcessAdmission, admission_id, with_for_update=True)
    _exact_scope(row, fence)
    admission = decode_row({key: getattr(row, key) for key in ROW_FIELDS})
    if admission.role != "native_evaluator" or admission.profile != PROFILE:
        raise ProcessError("oe_native_profile")
    state = session.get(ResearchProcessState, admission_id, with_for_update=True)
    _exact_scope(state, fence)
    receipts = {}
    for receipt in session.scalars(
        select(ResearchProcessReceipt).filter_by(admission_id=admission_id)
    ).all():
        _exact_scope(receipt, fence)
        if (
            receipt.kind in receipts
            or digest(receipt.payload) != receipt.payload_sha256
        ):
            raise ProcessError("oe_native_receipt")
        receipts[receipt.kind] = receipt
    if "launch_intent" not in receipts:
        raise ProcessError("oe_native_intent")
    intent = NativeIntent.model_validate_json(
        canonical(receipts["launch_intent"].payload)
    )
    if intent.admission != admission:
        raise ProcessError("oe_native_intent")
    return intent, state, receipts


def claim_native_launch(session, fence, admission_id, settings):
    intent, state, receipts = persisted(session, fence, admission_id)
    resolve_native_input(session, fence, intent, settings)
    _require_held(session, fence, intent.admission)
    if (
        state.status != "launch_intended"
        or set(receipts) != {"launch_intent"}
        or not intent.issued_at <= utcnow() < intent.not_after
    ):
        raise ProcessError("oe_native_claim")
    state.status, state.updated_at = "uncertain", utcnow()
    session.flush()
    return intent


def check_native_claim(session, fence, admission_id, *, expected_intent, settings):
    intent, state, receipts = persisted(session, fence, admission_id)
    resolve_native_input(session, fence, intent, settings)
    _require_held(session, fence, intent.admission)
    if (
        type(expected_intent) is not NativeIntent
        or NativeIntent.model_validate(expected_intent) != intent
        or state.status != "uncertain"
        or set(receipts) != {"launch_intent"}
        or not intent.issued_at <= utcnow() < intent.not_after
    ):
        raise ProcessError("oe_native_claim_changed")
    return intent


def record_native_identity(session, fence, admission_id, *, storage_root):
    intent, state, receipts = persisted(session, fence, admission_id)
    operation = operation_path(storage_root, intent.admission)
    launch = NativeLaunch.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "launch.json"))
    )
    identity = NativeIdentity.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "identity.json"))
    )
    fd = _directory(storage_root)
    try:
        root = os.fstat(fd)
    finally:
        os.close(fd)
    if (
        launch.intent != intent
        or (launch.root_device, launch.root_inode) != (root.st_dev, root.st_ino)
        or identity.launch_sha256 != digest(launch.model_dump(mode="json"))
        or identity.admission_sha256 != digest(intent.admission.model_dump(mode="json"))
        or identity.image_id != intent.admission.image_id
        or (identity.root_device, identity.root_inode)
        != (launch.root_device, launch.root_inode)
    ):
        raise ProcessError("oe_native_physical_identity")
    if state.status not in {"uncertain", "running", "stopped"} or (
        state.status == "stopped" and "launch_identity" not in receipts
    ):
        raise ProcessError("oe_native_identity_state")
    _receipt(
        session, intent.admission, "launch_identity", identity.model_dump(mode="json")
    )
    # No uncertain->running promotion from a detached historical file. Unit is
    # discoverable via the receipt; physical supervisor owns current live checks.
    session.flush()
    return identity


def record_native_stopped(
    session, fence, admission_id, *, storage_root, with_output=False
):
    intent, state, _receipts = persisted(session, fence, admission_id)
    launch, identity, stop, extraction = read_native_journal(
        storage_root, intent.admission, require_output=with_output
    )
    if launch.intent != intent or state.status not in {
        "launch_intended",
        "uncertain",
        "running",
        "stopped",
    }:
        raise ProcessError("oe_native_stop_identity")
    _receipt(
        session, intent.admission, "launch_identity", identity.model_dump(mode="json")
    )
    _receipt(session, intent.admission, "stop", stop.model_dump(mode="json"))
    if extraction is not None:
        _receipt(
            session, intent.admission, "extraction", extraction.model_dump(mode="json")
        )
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()
    return stop


def settle_native_conservatively(session, fence, admission_id, *, storage_root):
    intent, state, receipts = persisted(session, fence, admission_id)
    launch, _identity, stop, _ = read_native_journal(storage_root, intent.admission)
    if (
        state.status != "stopped"
        or "stop" not in receipts
        or launch.intent != intent
        or receipts["stop"].payload != stop.model_dump(mode="json")
    ):
        raise ProcessError("oe_native_settlement_stop")
    from backend.app.research_broker_models import ResearchBudgetReservation

    reservation = session.get(
        ResearchBudgetReservation, intent.admission.reservation_id
    )
    _exact_scope(reservation, fence)
    if reservation.purpose != "process_unit" or not BudgetVector.model_validate(
        reservation.amounts
    ).covers(FLOOR):
        raise ProcessError("oe_native_settlement_budget")
    return settle_budget(
        session,
        fence,
        reservation.id,
        charge=BudgetVector.model_validate(reservation.amounts),
        outcome="conservative_unknown",
    )
