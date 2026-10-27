"""Initial-epoch candidate/ACK ingress; evaluation execution remains separate.

No wire grant authorizes a process: every use resolves the retained candidate,
exact socket role, persisted intent and current worker phase afresh.
"""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app.openevolve_controller_events import _ticket
from backend.app.openevolve_controller_services import (
    require_search_controller,
    typed_bootstrap,
)
from backend.app.openevolve_event_identity import exact_event, require_event_ticket
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_services import (
    _owned_row,
    acknowledge_objective,
    register_candidate_intent,
)
from backend.app.openevolve_worker_actions import (
    _open_worker,
    apply_worker_observer_command,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.evaluator_protocol import EvaluationGrant
from packages.openevolve_adapter.host_contracts import (
    CandidateIntentCommand,
    EvidenceRef,
    ObjectiveAckCommand,
    SourceEnvelope,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from runtimes.openevolve_operational.profile import PROFILE


def _candidate_ticket(key, epoch_id, upstream_id):
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("Exact host candidate key required")
    return hmac.new(
        key, f"oe-candidate-v1:{epoch_id}:{upstream_id}".encode(), hashlib.sha256
    ).hexdigest()


def _authorize_intent(session, fence, peer, event, worker):
    require_event_ticket(session, fence, event)
    if worker is None:
        _launch, epoch = require_search_controller(
            session, fence, peer, epoch_id=event.epoch_id
        )
        if epoch.admission_id != event.admission_id:
            raise ValueError("Seed admission differs")
        return
    if (worker.epoch_id, worker.admission_id) != (event.epoch_id, event.admission_id):
        raise ValueError("Candidate worker scope differs")
    _open_worker(session, fence, peer, worker, PROFILE)


def register_candidate(factory, fence, peer, *, event, ticket_key, settings):
    exact_event(event)
    if event.kind != "candidate_intent" or set(event.payload) != {
        "grant",
        "upstream_program_id",
        "source",
    }:
        raise ValueError("Closed candidate intent required")
    upstream = UUID(event.payload["upstream_program_id"])
    if (
        str(upstream) != event.payload["upstream_program_id"]
        or type(event.payload["source"]) is not str
    ):
        raise ValueError("Canonical native candidate/source required")
    source = SourceEnvelope(
        source=event.payload["source"],
        source_sha256=hashlib.sha256(event.payload["source"].encode()).hexdigest(),
    )
    worker = (
        None
        if event.payload["grant"] is None
        else IterationGrant.model_validate_json(canonical(event.payload["grant"]))
    )
    ticket = _candidate_ticket(ticket_key, event.epoch_id, upstream)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        _authorize_intent(session, fence, peer, event, worker)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_source",
        content=canonical(source.model_dump(mode="json")),
        logical_key=f"oe-source:{event.epoch_id}:{upstream}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        _authorize_intent(session, fence, peer, event, worker)
        command = CandidateIntentCommand(
            epoch_id=event.epoch_id,
            admission_id=event.admission_id,
            upstream_program_id=upstream,
            phase="seed" if worker is None else "mutation",
            intent_id=None if worker is None else worker.intent_id,
            source=EvidenceRef(id=obj.id, sha256=obj.sha256),
            ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
            request_sha256=event.event_sha256,
        )
        if worker is None:
            row = register_candidate_intent(session, fence, command, settings)
        else:
            row = apply_worker_observer_command(
                session,
                fence,
                peer,
                worker,
                command,
                event="candidate_intent",
                expected_process_profile=PROFILE,
                settings=settings,
            )
        answer = EvaluationGrant(
            owner_id=fence.owner_id,
            run_id=fence.run_id,
            epoch_id=event.epoch_id,
            candidate_id=row.candidate_id,
            upstream_program_id=upstream,
            source_sha256=row.source_sha256,
            ticket=ticket,
            phase=row.phase,
        ).model_dump(mode="json")
        receipt = row.id
    return receipt, answer


def resolve_evaluation_peer(session, fence, peer, *, event, ticket_key):
    """Current phase binding only; never itself an evaluator launch permit."""
    require_event_ticket(session, fence, event)
    grant = EvaluationGrant.model_validate_json(canonical(event.payload["grant"]))
    if (grant.owner_id, grant.run_id, grant.epoch_id) != (
        fence.owner_id,
        fence.run_id,
        event.epoch_id,
    ):
        raise ValueError("Evaluation grant scope differs")
    candidate = _owned_row(session, m.SearchCandidate, grant.candidate_id, fence)
    row = session.scalar(
        select(m.SearchCandidateGrant).where(
            m.SearchCandidateGrant.candidate_id == candidate.id
        )
    )
    if row is None:
        raise ValueError("Candidate has no durable grant")
    _owned_row(session, m.SearchCandidateGrant, row.id, fence)
    if (
        (
            candidate.epoch_id,
            candidate.upstream_program_id,
            candidate.source_sha256,
            candidate.kind,
        )
        != (grant.epoch_id, grant.upstream_program_id, grant.source_sha256, grant.phase)
        or row.admission_id != event.admission_id
        or row.epoch_id != grant.epoch_id
        or not hmac.compare_digest(
            row.ticket_sha256, hashlib.sha256(grant.ticket.encode()).hexdigest()
        )
        or not hmac.compare_digest(
            grant.ticket,
            _candidate_ticket(ticket_key, grant.epoch_id, grant.upstream_program_id),
        )
    ):
        raise ValueError("Evaluation descriptor differs from exact retained candidate")
    worker = None
    if row.phase == "mutation":
        intent = _owned_row(session, m.SearchIterationIntent, row.intent_id, fence)
        worker = IterationGrant(
            schema_version="mineagent.openevolve-iteration-grant.v1",
            admission_id=event.admission_id,
            epoch_id=event.epoch_id,
            intent_id=intent.id,
            intent_ticket=_ticket(ticket_key, event.epoch_id, intent.id),
            native_iteration=intent.native_iteration,
            parent_candidate_id=intent.parent_candidate_id,
            parent_source_sha256=intent.parent_source_sha256,
            selection_sha256=intent.selection_sha256,
        )
    _authorize_intent(session, fence, peer, event, worker)
    return candidate, row, worker


def acknowledge_evaluation(factory, fence, peer, *, event, ticket_key, settings):
    exact_event(event)
    payload = event.payload
    if (
        event.kind != "evaluation_complete"
        or set(payload) != {"grant", "objective_receipt_id", "eligible", "metrics"}
        or type(payload["eligible"]) is not bool
        or type(payload["metrics"]) is not dict
    ):
        raise ValueError("Closed actual objective acknowledgement required")
    expected_keys = {"combined_score"} if payload["eligible"] else set()
    if set(payload["metrics"]) != expected_keys or (
        payload["eligible"]
        and type(payload["metrics"]["combined_score"]) not in (int, float)
    ):
        raise ValueError("Actual returned metrics differ")
    objective_id = UUID(payload["objective_receipt_id"])
    if str(objective_id) != payload["objective_receipt_id"]:
        raise ValueError("Canonical objective receipt required")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        _candidate, grant, worker = resolve_evaluation_peer(
            session, fence, peer, event=event, ticket_key=ticket_key
        )
        command = ObjectiveAckCommand(
            epoch_id=event.epoch_id,
            grant_id=grant.id,
            objective_receipt_id=objective_id,
            eligible=payload["eligible"],
            combined_score=payload["metrics"].get("combined_score"),
            request_sha256=event.event_sha256,
        )
        if worker is None:
            row = acknowledge_objective(session, fence, command)
        else:
            row = apply_worker_observer_command(
                session,
                fence,
                peer,
                worker,
                command,
                event="evaluation_complete",
                expected_process_profile=PROFILE,
                settings=settings,
            )
        receipt = row.id
    return receipt, {"acknowledged": True, "objective_receipt_id": str(objective_id)}


def check_seed_source(factory, fence, peer, *, event):
    exact_event(event)
    if event.kind != "seed_source" or set(event.payload) != {"source_sha256"}:
        raise ValueError("Closed seed source event required")
    with neutral_transaction(factory, fence) as session:
        require_event_ticket(session, fence, event)
        launch, epoch = require_search_controller(
            session, fence, peer, epoch_id=event.epoch_id
        )
        bootstrap, _raw = typed_bootstrap(launch)
        if (
            epoch.admission_id != event.admission_id
            or event.payload["source_sha256"]
            != bootstrap.spec.preparation.prepared.source_sha256
        ):
            raise ValueError("Observed seed differs from fixed prepared source")
        receipt = launch.id
    return receipt, {"matched": True}
