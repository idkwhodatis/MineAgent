"""Controller-attested scheduler placement → independent host kernel observation.

No arbitrary-descendant or first-ticket-holder assignment. The native scheduling
observation identifies one exact worker; host daemon/kernel reads verify it both
before evidence publication and again in the committing service transaction.
"""

import hashlib

from sqlalchemy import select

from backend.app.openevolve_controller_services import require_search_controller
from backend.app.openevolve_event_identity import exact_event, require_event_ticket
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_protected_workers import record_expected_worker
from backend.app.openevolve_services import _owned_row, _scope
from backend.app.openevolve_worker_models import SearchWorkerTicket
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.host_contracts import EvidenceRef
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.placement_contracts import (
    WorkerPlacementEvent,
    WorkerPlacementObservation,
)
from packages.openevolve_adapter.worker_identity_evidence import (
    KernelIdentity,
    ObservedWorker,
    WorkerIdentityEvidence,
)
from runtimes.openevolve_operational.worker_observation import observe_worker


def observe_placement(factory, fence, peer, *, event, settings):
    exact_event(event)
    if event.kind != "worker_placement":
        raise ValueError("Actual scheduling placement event required")
    placement = WorkerPlacementObservation.model_validate_json(canonical(event.payload))
    if placement.epoch_id != event.epoch_id:
        raise ValueError("Placement epoch differs")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _launch, epoch = require_search_controller(
            session, fence, peer, epoch_id=event.epoch_id
        )
        ticket = session.scalar(
            select(SearchWorkerTicket).where(
                SearchWorkerTicket.intent_id == placement.intent_id
            )
        )
        if ticket is None or (ticket.epoch_id, ticket.admission_id) != (
            epoch.id,
            event.admission_id,
        ):
            raise ValueError("Scheduler placement lacks prior durable worker ticket")
        _owned_row(session, SearchWorkerTicket, ticket.id, fence)
        ticket_id = ticket.id
    worker = observe_worker(peer._context, peer.binding, placement)
    normalized = WorkerPlacementEvent(
        schema_version="mineagent.openevolve-placement-event.v1",
        admission_id=event.admission_id,
        placement=placement,
    )
    fields = ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    evidence = WorkerIdentityEvidence(
        schema_version="mineagent.openevolve-worker-identity.v1",
        **_scope(fence),
        admission_id=event.admission_id,
        epoch_id=event.epoch_id,
        intent_id=placement.intent_id,
        ticket_id=ticket_id,
        placement_event_sha256=hashlib.sha256(
            canonical(normalized.model_dump(mode="json"))
        ).hexdigest(),
        placement=placement,
        controller=KernelIdentity(
            **{k: getattr(peer.binding.controller, k) for k in fields}
        ),
        worker=ObservedWorker(
            **{k: getattr(worker, k) for k in fields},
            namespace_pid=placement.worker_namespace_pid,
            parent_host_pid=worker.ppid,
        ),
    )
    obj = publish_search_object(
        factory,
        fence,
        kind="search_worker_identity",
        content=canonical(evidence.model_dump(mode="json")),
        logical_key=f"oe-worker-identity:{placement.intent_id}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        row = record_expected_worker(
            session,
            fence,
            peer,
            event=event,
            evidence_reference=EvidenceRef(id=obj.id, sha256=obj.sha256),
            settings=settings,
        )
        receipt = row.id
    return receipt, {
        "placement_receipt_id": str(receipt),
        "epoch_id": str(placement.epoch_id),
        "intent_id": str(placement.intent_id),
        "worker_namespace_pid": placement.worker_namespace_pid,
        "worker_start_ticks": placement.worker_start_ticks,
    }
