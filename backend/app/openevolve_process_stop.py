"""Exact persisted OE stop barrier; no filesystem, launch or recovery authority."""

from sqlalchemy import select

from backend.app.research_process_models import (
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_evaluator import journal as native
from runtimes.openevolve_evaluator.contracts import PROFILE as NATIVE
from runtimes.openevolve_operational import process_journal as controller


def stop_committed(session, row):
    try:
        admission = decode_row({k: getattr(row, k) for k in ROW_FIELDS})
        is_controller = admission.profile == controller.PROFILE
        if not is_controller and admission.profile != NATIVE:
            return False
        scope = ("owner_id", "run_id", "attempt_id", "generation")
        state = session.get(ResearchProcessState, row.id)
        if (
            state is None
            or state.status != "stopped"
            or any(getattr(state, k) != getattr(row, k) for k in scope)
        ):
            return False
        rows = session.scalars(
            select(ResearchProcessReceipt).filter_by(admission_id=row.id)
        ).all()
        receipts = {r.kind: r for r in rows}
        required = {"launch_intent", "launch_identity", "stop"}
        optional = "controller_child" if is_controller else "extraction"
        if len(rows) != len(receipts) or set(receipts) not in (
            required,
            required | {optional},
        ):
            return False
        if any(
            r.payload_sha256 != digest(r.payload)
            or any(getattr(r, k) != getattr(row, k) for k in scope)
            for r in rows
        ):
            return False
        if is_controller:
            controller.controller_admission(admission)
            intent = controller.ControllerIntent.model_validate_json(
                canonical(receipts["launch_intent"].payload)
            )
            identity = controller.ControllerIdentity.model_validate_json(
                canonical(receipts["launch_identity"].payload)
            )
            stop = controller.ControllerStop.model_validate_json(
                canonical(receipts["stop"].payload)
            )
            child = (
                controller.ControllerChild.model_validate_json(
                    canonical(receipts[optional].payload)
                )
                if optional in receipts
                else None
            )
            launch = controller.ControllerLaunch(
                schema_version="mineagent.oe-controller-launch.v1",
                intent=intent,
                runtime_name="mineagent-oe-controller-" + str(row.id),
                root_device=identity.root_device,
                root_inode=identity.root_inode,
            )
            controller.validate_records(launch, identity, child, stop)
        else:
            intent = native.NativeIntent.model_validate_json(
                canonical(receipts["launch_intent"].payload)
            )
            identity = native.NativeIdentity.model_validate_json(
                canonical(receipts["launch_identity"].payload)
            )
            stop = native.NativeStop.model_validate_json(
                canonical(receipts["stop"].payload)
            )
            extraction = (
                native.NativeExtraction.model_validate_json(
                    canonical(receipts[optional].payload)
                )
                if optional in receipts
                else None
            )
            launch = native.NativeLaunch(
                intent=intent,
                runtime_name="mineagent-oe-native-" + str(intent.id),
                root_device=identity.root_device,
                root_inode=identity.root_inode,
            )
            native.validate_native_journal(launch, identity, stop, extraction)
        return intent.admission == admission
    except (ValueError, TypeError, AttributeError, ProcessError):
        return False
