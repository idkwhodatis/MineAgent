"""Exact persisted protected-controller stop barrier; never a launch grant."""

from sqlalchemy import select

from backend.app.research_process_models import (
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.rdagent_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    ControllerStop,
    controller_admission,
    validate_records,
)


def controller_stop_committed(session, row):
    try:
        admission = controller_admission(
            decode_row({k: getattr(row, k) for k in ROW_FIELDS})
        )
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
        if (
            len(rows) != len(receipts)
            or set(receipts)
            not in (
                {"launch_intent", "launch_identity", "stop"},
                {"launch_intent", "launch_identity", "controller_child", "stop"},
            )
            or any(
                r.payload_sha256 != digest(r.payload)
                or any(getattr(r, k) != getattr(row, k) for k in scope)
                for r in rows
            )
        ):
            return False
        intent = ControllerIntent.model_validate_json(
            canonical(receipts["launch_intent"].payload)
        )
        identity = ControllerIdentity.model_validate_json(
            canonical(receipts["launch_identity"].payload)
        )
        child = (
            ControllerChild.model_validate_json(
                canonical(receipts["controller_child"].payload)
            )
            if "controller_child" in receipts
            else None
        )
        stop = ControllerStop.model_validate_json(canonical(receipts["stop"].payload))
        if intent.admission != admission:
            return False
        launch = ControllerLaunch(
            schema_version="mineagent.rd-controller-launch.v1",
            intent=intent,
            runtime_name="mineagent-rd-controller-" + str(row.id),
            root_device=identity.root_device,
            root_inode=identity.root_inode,
        )
        validate_records(launch, identity, child, stop)
        return True
    except (ValueError, TypeError, AttributeError, ProcessError):
        return False
