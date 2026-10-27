"""024 exact protected worker grant resolution, not a first-arrival issuer."""

import hashlib
import hmac

from sqlalchemy import select

from backend.app.openevolve_controller_services import require_search_worker_unit
from backend.app.openevolve_models import SearchIterationIntent
from backend.app.openevolve_services import _owned_row, _scope
from backend.app.openevolve_worker_models import (
    SearchWorkerExpectedIdentity,
    SearchWorkerTicket,
)
from backend.app.services import DomainError
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from runtimes.openevolve_operational.peer import Peer, WorkerBinding
from runtimes.openevolve_operational.profile import PROFILE


def live_worker(session, fence, peer, grant, expected_process_profile):
    if (
        expected_process_profile != PROFILE
        or type(peer) is not Peer
        or type(peer.binding) is not WorkerBinding
    ):
        raise DomainError("search_worker", "Exact protected worker required", 409)
    grant = IterationGrant.model_validate_json(grant.model_dump_json())
    _launch, epoch = require_search_worker_unit(
        session, fence, peer, epoch_id=grant.epoch_id, intent_id=grant.intent_id
    )
    intent = _owned_row(session, SearchIterationIntent, grant.intent_id, fence)
    if (
        grant.admission_id,
        intent.epoch_id,
        intent.native_iteration,
        intent.parent_candidate_id,
        intent.parent_source_sha256,
        intent.selection_sha256,
    ) != (
        epoch.admission_id,
        epoch.id,
        grant.native_iteration,
        grant.parent_candidate_id,
        grant.parent_source_sha256,
        grant.selection_sha256,
    ):
        raise DomainError("search_worker", "Worker intent drift", 409)
    ticket = session.scalar(select(SearchWorkerTicket).filter_by(intent_id=intent.id))
    expected = session.scalar(
        select(SearchWorkerExpectedIdentity).filter_by(intent_id=intent.id)
    )
    if (
        ticket is None
        or expected is None
        or any(
            getattr(row, k) != v
            for row in (ticket, expected)
            for k, v in _scope(fence).items()
        )
        or (
            ticket.epoch_id,
            ticket.admission_id,
            expected.epoch_id,
            expected.admission_id,
            expected.ticket_id,
        )
        != (epoch.id, epoch.admission_id, epoch.id, epoch.admission_id, ticket.id)
    ):
        raise DomainError("search_worker", "Persisted worker scope differs", 409)
    actual_hash = hashlib.sha256(grant.intent_ticket.encode()).hexdigest()
    if (
        not hmac.compare_digest(ticket.ticket_sha256, actual_hash)
        or peer.binding.ticket_sha256 != ticket.ticket_sha256
    ):
        raise DomainError("search_worker", "Exact intent ticket differs", 409)
    observed = {
        key: getattr(peer.binding.worker, key)
        for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    }
    if any(getattr(expected, k) != v for k, v in observed.items()):
        raise DomainError("search_worker", "Expected native worker differs", 409)
    return epoch, intent, ticket, expected, observed


def record_expected_worker(
    session, fence, peer, *, event, evidence_reference, settings
):
    """Persist exact already-retained placement after repeated live observation."""
    from uuid import uuid4

    from backend.app.openevolve_controller_services import require_search_controller
    from backend.app.openevolve_services import _read_evidence
    from packages.openevolve_adapter.checkpoint_native import canonical
    from packages.openevolve_adapter.placement_contracts import WorkerPlacementEvent
    from packages.openevolve_adapter.worker_identity_evidence import (
        WorkerIdentityEvidence,
    )
    from runtimes.openevolve_operational.wire import Event
    from runtimes.openevolve_operational.worker_observation import observe_worker

    if type(event) is not Event or event.kind != "worker_placement":
        raise ValueError("fixed placement event")
    _launch, epoch = require_search_controller(
        session, fence, peer, epoch_id=event.epoch_id
    )
    evidence = WorkerIdentityEvidence.model_validate_json(
        canonical(
            _read_evidence(
                session, fence, evidence_reference, "search_worker_identity", settings
            )
        )
    )
    expected_event = WorkerPlacementEvent(
        schema_version="mineagent.openevolve-placement-event.v1",
        admission_id=epoch.admission_id,
        placement=evidence.placement,
    )
    controller = {
        key: getattr(peer.binding.controller, key)
        for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    }
    if (
        event.admission_id != epoch.admission_id
        or event.payload != evidence.placement.model_dump(mode="json")
        or any(getattr(evidence, k) != v for k, v in _scope(fence).items())
        or evidence.controller.model_dump() != controller
        or (evidence.epoch_id, evidence.admission_id) != (epoch.id, epoch.admission_id)
        or evidence.placement_event_sha256
        != hashlib.sha256(canonical(expected_event.model_dump(mode="json"))).hexdigest()
    ):
        raise ValueError("placement retained evidence differs")
    if not hmac.compare_digest(
        hashlib.sha256(event.process_ticket.encode()).hexdigest(),
        peer.admission.ipc.ticket_sha256,
    ):
        raise ValueError("placement process ticket")
    intent = _owned_row(session, SearchIterationIntent, evidence.intent_id, fence)
    ticket = _owned_row(session, SearchWorkerTicket, evidence.ticket_id, fence)
    if (intent.epoch_id, ticket.epoch_id, ticket.intent_id, ticket.admission_id) != (
        epoch.id,
        epoch.id,
        intent.id,
        epoch.admission_id,
    ):
        raise ValueError("placement intent ticket scope")
    worker = {
        key: getattr(evidence.worker, key)
        for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    }
    previous = session.scalar(
        select(SearchWorkerExpectedIdentity).filter_by(intent_id=intent.id)
    )
    if previous is not None:
        if (
            previous.ticket_id != ticket.id
            or previous.evidence_object_id != evidence_reference.id
            or previous.evidence_sha256 != evidence_reference.sha256
            or any(getattr(previous, k) != v for k, v in worker.items())
        ):
            raise ValueError("placement exact replay drift")
        return previous
    actual = observe_worker(peer._context, peer.binding, evidence.placement)
    if (
        any(getattr(actual, k) != v for k, v in worker.items())
        or actual.ppid != evidence.worker.parent_host_pid
    ):
        raise ValueError("placement changed before append")
    require_search_controller(session, fence, peer, epoch_id=epoch.id)
    row = SearchWorkerExpectedIdentity(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        admission_id=epoch.admission_id,
        intent_id=intent.id,
        ticket_id=ticket.id,
        **worker,
        evidence_object_id=evidence_reference.id,
        evidence_kind="search_worker_identity",
        evidence_sha256=evidence_reference.sha256,
    )
    session.add(row)
    session.flush()
    return row
