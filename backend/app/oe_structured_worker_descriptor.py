"""Exact host-retained descriptor check, not ticket-only worker authority."""

import hashlib
from dataclasses import asdict

from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_sampling_models import (
    OEStructuredSamplingIntent,
    OEStructuredWorkerPlacement,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.host_events import WorkerDescriptor
from runtimes.openevolve_structured_host.peer import WorkerBinding


def require_worker_descriptor(session, fence, descriptor, settings, *, peer):
    """Return scoped partial sampling only after exact real-peer/placement checks."""
    if type(descriptor) is not dict or type(peer.binding) is not WorkerBinding:
        fail("oe_structured_worker_descriptor")
    raw = canonical_bytes(descriptor)
    if len(raw) > 8192:
        fail("oe_structured_worker_descriptor")
    value = WorkerDescriptor.model_validate_json(raw)
    sampling = scoped(session.get(OEStructuredSamplingIntent, value.intent_id), fence)
    context = require_controller_context(
        session, fence, sampling.epoch_id, settings, peer=peer, intent_id=sampling.id
    )
    parent = scoped(
        session.get(OEStructuredCandidate, sampling.parent_candidate_id), fence
    )
    placement = scoped(
        session.scalar(
            select(OEStructuredWorkerPlacement).filter_by(
                sampling_intent_id=sampling.id
            )
        ),
        fence,
    )
    latest = scoped(
        session.scalar(
            select(OEStructuredControlState)
            .filter_by(epoch_id=sampling.epoch_id)
            .order_by(OEStructuredControlState.sequence.desc())
            .limit(1)
        ),
        fence,
    )
    if (
        value.admission_id != context.epoch.controller_admission_id
        or value.epoch_id != sampling.epoch_id
        or value.native_iteration != sampling.ordinal
        or value.parent_candidate_id != parent.id
        or parent.epoch_id != sampling.epoch_id
        or not 0 <= parent.ordinal < sampling.ordinal
        or value.parent_candidate_sha256 != parent.candidate_sha256
        or value.selection_sha256 != sampling.selection_sha256
        or hashlib.sha256(value.intent_ticket.encode("ascii")).hexdigest()
        != sampling.worker_ticket_sha256
        or peer.binding.ticket_sha256 != sampling.worker_ticket_sha256
        or (peer.binding.epoch_id, peer.binding.intent_id)
        != (sampling.epoch_id, sampling.id)
        or placement.sampling_intent_id != sampling.id
        or placement.worker_identity_canonical
        != canonical_bytes(asdict(peer.binding.worker)).decode("ascii")
        or placement.worker_identity_sha256 != digest(asdict(peer.binding.worker))
        or placement.controller_identity_sha256
        != digest(asdict(peer.binding.controller))
        or latest.id != sampling.sampling_state_id
        or latest.phase != "sampling"
        or latest.sequence != 2 * sampling.ordinal
        or latest.frontier != sampling.ordinal - 1
    ):
        fail("oe_structured_worker_descriptor")
    require_controller_context(
        session, fence, sampling.epoch_id, settings, peer=peer, intent_id=sampling.id
    )
    return sampling
