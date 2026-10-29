"""Real scheduler placement, independently measured before durable worker use."""

from dataclasses import asdict
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_sampling_models import (
    OEStructuredSamplingIntent,
    OEStructuredWorkerPlacement,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.host_events import WorkerPlacementObservation
from runtimes.openevolve_structured_host.peer import ControllerBinding, WorkerBinding
from runtimes.openevolve_structured_host.worker_observation import observe_worker
from runtimes.rdagent_operational.peer import KernelIdentity


def record_placement(session, fence, epoch_id, raw, settings, *, peer):
    """Flush-only under caller lifecycle/native guards; ACK only after commit.

    Controller socket proof is distinct from the observed pool worker. A worker
    ticket alone is never allowed to select whichever process arrives first.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 8192:
        fail()
    placement = WorkerPlacementObservation.model_validate_json(raw)
    if canonical_bytes(placement) != raw or placement.epoch_id != epoch_id:
        fail()
    context = require_controller_context(session, fence, epoch_id, settings, peer=peer)
    if type(peer.binding) is not ControllerBinding:
        fail()
    sampling = scoped(
        session.get(OEStructuredSamplingIntent, placement.intent_id), fence
    )
    if sampling.epoch_id != epoch_id:
        fail()
    cursor = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    scoped(cursor, fence)
    if (
        cursor.id != sampling.sampling_state_id
        or cursor.phase != "sampling"
        or cursor.sequence != 2 * sampling.ordinal
        or cursor.frontier != sampling.ordinal - 1
    ):
        fail()
    worker = observe_worker(peer._context, peer.binding, placement)
    # Full lineage/type validation before any row can act as expected placement.
    binding = WorkerBinding(
        peer.binding, epoch_id, sampling.id, sampling.worker_ticket_sha256, worker
    )
    identity_raw = canonical_bytes(asdict(binding.worker))
    row = session.scalar(
        select(OEStructuredWorkerPlacement).filter_by(sampling_intent_id=sampling.id)
    )
    expected = {
        **{
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        "sampling_intent_id": sampling.id,
        "placement_canonical": raw.decode("ascii"),
        "placement_sha256": digest(placement),
        "worker_identity_canonical": identity_raw.decode("ascii"),
        "worker_identity_sha256": digest(asdict(worker)),
        "controller_identity_sha256": digest(asdict(peer.binding)),
    }
    if row is not None:
        scoped(row, fence)
        if any(getattr(row, key) != value for key, value in expected.items()):
            fail()
        if (
            TypeAdapter(KernelIdentity).validate_json(row.worker_identity_canonical)
            != worker
        ):
            fail()
    else:
        row = OEStructuredWorkerPlacement(id=uuid4(), **expected)
        session.add(row)
        session.flush()
    latest = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if (
        latest is None
        or latest.id != cursor.id
        or observe_worker(peer._context, peer.binding, placement) != worker
        or require_controller_context(session, fence, epoch_id, settings, peer=peer)
        != context
    ):
        fail()
    return row
