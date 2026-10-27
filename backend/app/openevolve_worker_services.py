"""Unregistered worker assignment only; no general worker event dispatcher.

All methods are flush-only. A live descendant plus ticket is insufficient after
assignment closure or for a different intent. No broker/evaluator context is
issued by these methods. Render/proposal/evaluation phase receipts remain closed.
"""

import hashlib
import hmac
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select

from backend.app.openevolve_event_models import SearchControllerEventReceipt
from backend.app.openevolve_models import SearchIterationIntent, SearchWorkerOutcome
from backend.app.openevolve_services import _locked_epoch, _owned_row, _scope
from backend.app.openevolve_worker_models import (
    SearchWorkerAssignment,
    SearchWorkerAssignmentClosure,
    SearchWorkerExpectedIdentity,
    SearchWorkerTicket,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import require_running_peer
from backend.app.services import DomainError
from packages.openevolve_adapter.contracts import SHA
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from runtimes.research_supervisor.ipc import require_authenticated_peer


def register_worker_ticket(
    session, fence, *, intent_id, controller_receipt_id, ticket_sha256
):
    require_neutral_transaction(session, fence)
    TypeAdapter(SHA).validate_python(ticket_sha256, strict=True)
    intent = _owned_row(session, SearchIterationIntent, intent_id, fence)
    epoch = _locked_epoch(session, fence, intent.epoch_id)
    receipt = _owned_row(
        session, SearchControllerEventReceipt, controller_receipt_id, fence
    )
    if (
        receipt.epoch_id != epoch.id
        or receipt.intent_id != intent.id
        or receipt.admission_id != epoch.admission_id
        or receipt.event_kind != "iteration_intent"
    ):
        raise DomainError(
            "search_worker", "Ticket lacks exact committed sampling identity", 409
        )
    existing = session.scalar(
        select(SearchWorkerTicket).where(SearchWorkerTicket.intent_id == intent.id)
    )
    if existing is not None:
        if (
            existing.ticket_sha256 != ticket_sha256
            or existing.controller_receipt_id != receipt.id
        ):
            raise DomainError("search_replay", "Conflicting worker ticket", 409)
        return existing
    row = SearchWorkerTicket(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        admission_id=epoch.admission_id,
        intent_id=intent.id,
        controller_receipt_id=receipt.id,
        ticket_sha256=ticket_sha256,
    )
    session.add(row)
    session.flush()
    return row


def _live_worker(session, fence, authorization, grant, expected_process_profile):
    from runtimes.openevolve_operational.profile import PROFILE

    if expected_process_profile == PROFILE:
        from backend.app.openevolve_protected_workers import live_worker

        return live_worker(
            session, fence, authorization, grant, expected_process_profile
        )
    require_neutral_transaction(session, fence)
    grant = IterationGrant.model_validate(grant.model_dump(mode="python"))
    epoch = _locked_epoch(session, fence, grant.epoch_id)
    admission = _owned_row(session, ResearchProcessAdmission, epoch.admission_id, fence)
    running = require_running_peer(
        session, fence, authorization, admission.logical_operation_id
    )
    peer = require_authenticated_peer(
        authorization, fence, logical_operation_id=admission.logical_operation_id
    )
    if (
        running.id != epoch.admission_id
        or grant.admission_id != epoch.admission_id
        or running.profile != expected_process_profile
        or dict(peer.peer) == dict(peer.leader)
    ):
        raise DomainError(
            "search_worker", "Exact live descendant assignment required", 409
        )
    intent = _owned_row(session, SearchIterationIntent, grant.intent_id, fence)
    if (
        intent.epoch_id,
        intent.native_iteration,
        intent.parent_candidate_id,
        intent.parent_source_sha256,
        intent.selection_sha256,
    ) != (
        epoch.id,
        grant.native_iteration,
        grant.parent_candidate_id,
        grant.parent_source_sha256,
        grant.selection_sha256,
    ):
        raise DomainError(
            "search_worker", "Worker grant differs from immutable intent", 409
        )
    ticket = session.scalar(
        select(SearchWorkerTicket).where(SearchWorkerTicket.intent_id == intent.id)
    )
    if (
        ticket is None
        or ticket.epoch_id != epoch.id
        or ticket.admission_id != epoch.admission_id
        or any(getattr(ticket, key) != value for key, value in _scope(fence).items())
        or not hmac.compare_digest(
            ticket.ticket_sha256,
            hashlib.sha256(grant.intent_ticket.encode()).hexdigest(),
        )
    ):
        raise DomainError("search_worker", "Worker intent ticket differs", 409)
    expected = session.scalar(
        select(SearchWorkerExpectedIdentity).where(
            SearchWorkerExpectedIdentity.intent_id == intent.id
        )
    )
    if (
        expected is None
        or expected.ticket_id != ticket.id
        or expected.epoch_id != epoch.id
        or expected.admission_id != epoch.admission_id
        or any(getattr(expected, key) != value for key, value in _scope(fence).items())
        or not _same_peer(expected, dict(peer.peer))
    ):
        raise DomainError(
            "search_worker", "Expected worker placement is not authorized", 409
        )
    # No enabled issuance path exists. The separate flush-only issuance draft
    # requires authenticated controller placement plus repeated kernel evidence;
    # first-arrival possession of an intent ticket cannot create that authority.
    return epoch, intent, ticket, expected, dict(peer.peer)


def _same_peer(assignment, observed):
    return all(
        getattr(assignment, key) == observed[key]
        for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
    )


def _closure(session, assignment_id):
    return session.scalar(
        select(SearchWorkerAssignmentClosure).where(
            SearchWorkerAssignmentClosure.assignment_id == assignment_id
        )
    )


def bind_started_worker(
    session, fence, authorization, grant, *, expected_process_profile
):
    epoch, intent, ticket, expected, observed = _live_worker(
        session, fence, authorization, grant, expected_process_profile
    )
    existing = session.scalar(
        select(SearchWorkerAssignment).where(
            SearchWorkerAssignment.intent_id == intent.id
        )
    )
    if existing is not None:
        if (
            not _same_peer(existing, observed)
            or existing.ticket_id != ticket.id
            or existing.expected_identity_id != expected.id
            or _closure(session, existing.id) is not None
        ):
            raise DomainError(
                "search_worker", "Worker assignment differs or is closed", 409
            )
        return existing
    # Fixed profile has one worker. Require ALL earlier assignments in this unit
    # closed, including crashed worker identities or recycled host PID values.
    assignments = session.scalars(
        select(SearchWorkerAssignment).where(
            SearchWorkerAssignment.admission_id == epoch.admission_id
        )
    ).all()
    if any(_closure(session, row.id) is None for row in assignments):
        raise DomainError(
            "search_worker", "Prior unit worker assignment remains open", 409
        )
    prior_intents = session.scalars(
        select(SearchIterationIntent).where(
            SearchIterationIntent.epoch_id == epoch.id,
            SearchIterationIntent.native_iteration < intent.native_iteration,
        )
    ).all()
    if {row.native_iteration for row in prior_intents} != set(
        range(1, intent.native_iteration)
    ) or {row.intent_id for row in assignments} != {row.id for row in prior_intents}:
        raise DomainError(
            "search_worker", "Prior charged intent lacks closed ordered assignment", 409
        )
    if (
        set(observed) != {"host_pid", "start_ticks", "pid_namespace", "cgroup"}
        or any(
            type(observed[key]) is not int or observed[key] <= 0
            for key in ("host_pid", "start_ticks")
        )
        or type(observed["pid_namespace"]) is not str
        or not 0 < len(observed["pid_namespace"]) <= 128
        or type(observed["cgroup"]) is not str
        or not 0 < len(observed["cgroup"]) <= 4096
    ):
        raise DomainError(
            "search_worker", "Observed kernel identity is not closed", 409
        )
    row = SearchWorkerAssignment(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        admission_id=epoch.admission_id,
        intent_id=intent.id,
        ticket_id=ticket.id,
        expected_identity_id=expected.id,
        **observed,
    )
    session.add(row)
    session.flush()
    return row


def close_worker_assignment(
    session, fence, authorization, grant, *, worker_outcome_id, expected_process_profile
):
    epoch, intent, ticket, expected, observed = _live_worker(
        session, fence, authorization, grant, expected_process_profile
    )
    assignment = session.scalar(
        select(SearchWorkerAssignment).where(
            SearchWorkerAssignment.intent_id == intent.id
        )
    )
    if (
        assignment is None
        or assignment.ticket_id != ticket.id
        or assignment.expected_identity_id != expected.id
        or not _same_peer(assignment, observed)
    ):
        raise DomainError("search_worker", "Outcome lacks exact assigned worker", 409)
    outcome = _owned_row(session, SearchWorkerOutcome, worker_outcome_id, fence)
    if outcome.epoch_id != epoch.id or outcome.intent_id != intent.id:
        raise DomainError(
            "search_worker", "Closure outcome differs from assigned intent", 409
        )
    existing = _closure(session, assignment.id)
    if existing is not None:
        if existing.worker_outcome_id != outcome.id:
            raise DomainError(
                "search_replay", "Conflicting worker assignment closure", 409
            )
        return existing  # exact historical closure; creates no event side effect
    row = SearchWorkerAssignmentClosure(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        intent_id=intent.id,
        assignment_id=assignment.id,
        worker_outcome_id=outcome.id,
        native_iteration=intent.native_iteration,
    )
    session.add(row)
    session.flush()
    return row
