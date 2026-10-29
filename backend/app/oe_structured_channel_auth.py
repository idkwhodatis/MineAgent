"""Bind an actual normal027 Unix socket to persisted independently observed IDs.

Wire IDs select scoped rows; they never supply expected PIDs or grant authority.
The final Peer comes only from SO_PEERCRED plus the exact daemon-bound observer.
Caller holds shared lifecycle/native guard and a neutral transaction throughout.
"""

import time
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_controller_process_services import _persisted
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_epoch_services import expected_controller_bootstrap
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_sampling_models import (
    OEStructuredSamplingIntent,
    OEStructuredWorkerPlacement,
)
from packages.factors.contracts import canonical_bytes, digest
from runtimes.openevolve_structured_host.peer import (
    ControllerBinding,
    WorkerBinding,
    authenticate,
)
from runtimes.openevolve_structured_host.process_journal import read_controller_journal
from runtimes.openevolve_structured_host.supervisor import RunningController
from runtimes.rdagent_operational.peer import KernelIdentity


def authenticate_channel(
    session,
    fence,
    epoch_id,
    settings,
    channel,
    *,
    process_ticket,
    running,
    sampling_id=None,
    worker_ticket=None,
):
    """Return a real ephemeral Peer, after fresh journal/scope/placement checks.

    Controller events omit sampling_id; worker events must identify their exact
    previously ACKed sampling and separately measured placement. No first-arrival
    worker nomination, inherited peer, deserialized peer or caller PID is allowed.
    """
    if (
        type(running) is not RunningController
        or type(epoch_id) is not UUID
        or (sampling_id is not None and type(sampling_id) is not UUID)
    ):
        fail("oe_structured_channel_identity")
    epoch, bootstrap, _ = expected_controller_bootstrap(
        session, fence, epoch_id, settings
    )
    admission, state, receipts, intent = _persisted(
        session, fence, epoch.controller_admission_id
    )
    if (
        state.status != "running"
        or admission.logical_operation_id != epoch.id
        or set(receipts) != {"launch_intent", "launch_identity", "controller_child"}
    ):
        fail("oe_structured_channel_not_running")
    launch, identity, child, _ = read_controller_journal(
        settings.storage_root, admission, with_child=True
    )
    expected = {
        "launch_intent": intent,
        "launch_identity": identity,
        "controller_child": child,
    }
    if (
        launch.intent != intent
        or intent.bootstrap_sha256 != epoch.bootstrap_sha256
        or (intent.issued_at, intent.not_after)
        != (bootstrap.issued_at, bootstrap.not_after)
        or any(
            receipts[k].payload != v.model_dump(mode="json")
            for k, v in expected.items()
        )
    ):
        fail("oe_structured_channel_physical_drift")
    controller = ControllerBinding(
        identity.container_id,
        identity.admission_sha256,
        identity.leader,
        child.controller,
    )
    binding = controller
    if sampling_id is not None:
        sampling = scoped(session.get(OEStructuredSamplingIntent, sampling_id), fence)
        placement = scoped(
            session.scalar(
                select(OEStructuredWorkerPlacement).filter_by(
                    sampling_intent_id=sampling_id
                )
            ),
            fence,
        )
        cursor = scoped(
            session.scalar(
                select(OEStructuredControlState)
                .filter_by(epoch_id=epoch.id)
                .order_by(OEStructuredControlState.sequence.desc())
                .limit(1)
            ),
            fence,
        )
        if (
            sampling.epoch_id != epoch.id
            or placement.sampling_intent_id != sampling.id
            or placement.controller_identity_sha256 != digest(asdict(controller))
            or cursor.id != sampling.sampling_state_id
            or cursor.phase != "sampling"
            or cursor.sequence != 2 * sampling.ordinal
            or cursor.frontier != sampling.ordinal - 1
        ):
            fail("oe_structured_channel_worker_cursor")
        worker = TypeAdapter(KernelIdentity).validate_json(
            placement.worker_identity_canonical
        )
        if (
            canonical_bytes(asdict(worker)).decode("ascii")
            != placement.worker_identity_canonical
            or digest(asdict(worker)) != placement.worker_identity_sha256
        ):
            fail("oe_structured_channel_worker_identity")
        binding = WorkerBinding(
            controller, epoch.id, sampling.id, sampling.worker_ticket_sha256, worker
        )
    elif worker_ticket is not None:
        fail("oe_structured_channel_unexpected_worker_ticket")
    # The exact live supervisor owns the original monotonic deadline. Rebuilding
    # it from wall-clock time here could accidentally extend a long-running unit.
    observer = running.observer_context
    if (
        running.launch != launch
        or running.identity != identity
        or running.child != child
        or running.finished
        or running._stop_started
        or Path(running.root).absolute() != Path(settings.storage_root).absolute()
        or running.supervisor.image_id != admission.image_id
        or observer.container_id != identity.container_id
        or observer.image_id != identity.image_id
        or observer.leader_pid != identity.leader.host_pid
        or observer.deadline != running.deadline
        or time.monotonic() >= running.deadline
    ):
        fail("oe_structured_channel_original_runtime")
    peer = authenticate(
        channel,
        admission,
        binding=binding,
        context=observer,
        process_ticket=process_ticket,
        worker_ticket=worker_ticket,
    )
    # Re-read full graph/physical journals and actual peer after authentication.
    require_controller_context(
        session, fence, epoch.id, settings, peer=peer, intent_id=sampling_id
    )
    return peer
