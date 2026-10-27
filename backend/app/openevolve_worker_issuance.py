"""Unregistered flush-only expected-worker persistence after host observation.

The outside-I/O stage obtains a bounded PID inventory from docker top on the exact
committed unit, invokes observe_pool_worker, and finalizes its closed evidence
object. None of those observations/DTOs is authority. This boundary freshly checks
live controller+fence, exact intent ticket and repeated current kernel lineage.
No listener, Docker call, object publication, broker context or ACK is performed.
"""

import hashlib
import hmac
from uuid import uuid4

from sqlalchemy import select

from backend.app.engine_peer_services import ControllerBinding, require_controller_peer
from backend.app.openevolve_models import SearchIterationIntent
from backend.app.openevolve_services import (
    _locked_epoch,
    _owned_row,
    _read_evidence,
    _scope,
)
from backend.app.openevolve_worker_models import (
    SearchWorkerExpectedIdentity,
    SearchWorkerTicket,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import EvidenceRef
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.placement_contracts import parse_placement_wire
from packages.openevolve_adapter.worker_identity_evidence import WorkerIdentityEvidence
from runtimes.research_supervisor.ipc import require_authenticated_peer
from runtimes.research_supervisor.worker_placement import observe_pool_worker


def record_expected_worker_identity(
    session,
    fence,
    authorization,
    placement_wire,
    *,
    evidence_reference,
    expected_process_profile,
    settings,
):
    require_neutral_transaction(session, fence)
    event, event_sha256, process_ticket = parse_placement_wire(placement_wire)
    epoch = _locked_epoch(session, fence, event.placement.epoch_id)
    admission = _owned_row(session, ResearchProcessAdmission, epoch.admission_id, fence)
    if not hmac.compare_digest(
        hashlib.sha256(process_ticket.encode()).hexdigest(), admission.ticket_sha256
    ):
        raise DomainError("search_worker", "Placement process ticket differs", 409)
    binding = ControllerBinding(
        admission_id=epoch.admission_id,
        role="openevolve_orchestrator",
        profile=expected_process_profile,
        image_id=admission.image_id,
        bootstrap_sha256=epoch.bootstrap_sha256,
    )
    require_controller_peer(session, fence, authorization, binding=binding)
    peer = require_authenticated_peer(
        authorization, fence, logical_operation_id=admission.logical_operation_id
    )
    if event.admission_id != epoch.admission_id:
        raise DomainError("search_worker", "Placement event unit differs", 409)
    reference = EvidenceRef.model_validate(evidence_reference.model_dump(mode="python"))
    evidence = WorkerIdentityEvidence.model_validate_json(
        canonical(
            _read_evidence(
                session, fence, reference, "search_worker_identity", settings
            )
        )
    )
    if (
        evidence.placement != event.placement
        or evidence.admission_id != event.admission_id
        or evidence.placement_event_sha256 != event_sha256
    ):
        raise DomainError(
            "search_worker",
            "Evidence differs from authenticated placement message",
            409,
        )
    if any(getattr(evidence, key) != value for key, value in _scope(fence).items()):
        raise DomainError("search_worker", "Expected placement scope differs", 409)
    if (
        evidence.admission_id != epoch.admission_id
        or evidence.controller.model_dump() != dict(peer.leader)
    ):
        raise DomainError(
            "search_worker", "Placement controller differs from live unit", 409
        )
    intent = _owned_row(session, SearchIterationIntent, evidence.intent_id, fence)
    ticket = _owned_row(session, SearchWorkerTicket, evidence.ticket_id, fence)
    if intent.epoch_id != epoch.id or (
        ticket.epoch_id,
        ticket.intent_id,
        ticket.admission_id,
    ) != (epoch.id, intent.id, epoch.admission_id):
        raise DomainError("search_worker", "Placement intent/ticket differs", 409)
    existing = session.scalar(
        select(SearchWorkerExpectedIdentity).where(
            SearchWorkerExpectedIdentity.intent_id == intent.id
        )
    )
    worker_identity = {
        key: getattr(evidence.worker, key)
        for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    }
    if existing is not None:
        if (
            existing.ticket_id != ticket.id
            or existing.evidence_object_id != reference.id
            or existing.evidence_sha256 != reference.sha256
            or any(
                getattr(existing, key) != value
                for key, value in worker_identity.items()
            )
        ):
            raise DomainError(
                "search_replay", "Conflicting expected worker placement", 409
            )
        return existing  # historical immutable replay, no new authority/effect
    # This is a narrow recheck of the already host-observed candidate, NOT a
    # client PID list or replacement for the outside exact-unit inventory stage.
    observed = observe_pool_worker(
        dict(peer.leader),
        evidence.placement.worker_namespace_pid,
        evidence.placement.worker_start_ticks,
        (peer.leader["host_pid"], evidence.worker.host_pid),
        controller_namespace_pid=evidence.placement.controller_namespace_pid,
    )
    if observed != evidence.worker.model_dump():
        raise DomainError(
            "search_worker", "Worker kernel identity changed before persistence", 409
        )
    require_controller_peer(session, fence, authorization, binding=binding)
    row = SearchWorkerExpectedIdentity(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        admission_id=epoch.admission_id,
        intent_id=intent.id,
        ticket_id=ticket.id,
        **worker_identity,
        evidence_object_id=reference.id,
        evidence_kind="search_worker_identity",
        evidence_sha256=reference.sha256,
    )
    session.add(row)
    session.flush()
    return row
