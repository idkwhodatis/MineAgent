"""Exact persisted normal027 absence barrier, distinct from successful completion.

The recovery caller separately rereads physical journals. This predicate cannot
turn missing completion into success, nor launch, settle or revive a lease.
"""

from sqlalchemy import select

from backend.app.research_process_models import (
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_structured_host.process_journal import (
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
                {
                    "launch_intent",
                    "launch_identity",
                    "controller_child",
                    "stop",
                    "controller_completion",
                },
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
            schema_version="mineagent.oe-structured-controller-launch.v1",
            intent=intent,
            runtime_name="mineagent-oe-structured-controller-" + str(row.id),
            root_device=identity.root_device,
            root_inode=identity.root_inode,
        )
        validate_records(launch, identity, child, stop)
        if "controller_completion" in receipts:
            from runtimes.openevolve_structured_host.completion_journal import (
                ControllerCompletion,
                validate_completion,
            )

            completion = ControllerCompletion.model_validate_json(
                canonical(receipts["controller_completion"].payload)
            )
            validate_completion(completion, launch, identity, child, stop)
        return True
    except (ValueError, TypeError, AttributeError, ProcessError):
        return False
