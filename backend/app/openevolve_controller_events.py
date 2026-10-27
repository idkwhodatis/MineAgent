"""Actual retained controller events; no listener or public issuer.

Evidence publication is separately journaled under the current fence. Every
semantic mutation repeats live controller authentication in a fresh transaction;
only its committed immutable receipt becomes an ACK. Interrupted publication
retains its charge and cannot advance the native control cursor.
"""

import hashlib
import hmac

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app.openevolve_controller_services import require_search_controller
from backend.app.openevolve_event_identity import require_event_ticket
from backend.app.openevolve_event_projection import (
    inspect_authenticated_controller_event,
)
from backend.app.openevolve_event_services import record_controller_event
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_services import _owned_row, prepare_checkpoint
from backend.app.openevolve_worker_services import register_worker_ticket
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.event_projection import (
    SamplingPayload,
    evidence_projections,
    sampling_command,
    terminal_command,
)
from packages.openevolve_adapter.host_contracts import (
    CheckpointIntentCommand,
    EvidenceRef,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from packages.research_runtime.budget_contracts import BudgetVector
from runtimes.openevolve_operational.profile import PROFILE
from runtimes.openevolve_operational.wire import Event, parse_event


def _wire(event):
    if type(event) is not Event or event.kind not in {
        "iteration_intent",
        "terminal_applied",
        "checkpoint_prepare",
    }:
        raise ValueError("Exact controller semantic event required")
    import json

    raw = canonical(
        {
            "event": json.loads(event.raw_event),
            "event_sha256": event.event_sha256,
            "process_ticket": event.process_ticket,
        }
    )
    if parse_event(raw) != event:
        raise ValueError("Parsed event fields differ from immutable wire")
    return raw


def _ticket(key, epoch_id, intent_id):
    # Host-owned per-unit key, never a request argument or replacement for peer
    # identity. Same immutable intent returns the same lost-ACK descriptor.
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("Host ticket key must contain exactly 32 bytes")
    return hmac.new(
        key, f"oe-worker-v1:{epoch_id}:{intent_id}".encode(), hashlib.sha256
    ).hexdigest()


def _ref(value):
    return EvidenceRef(id=value.id, sha256=value.sha256)


def apply_controller_event(factory, fence, peer, *, event, ticket_key, settings):
    wire = _wire(event)
    if event.kind not in {"iteration_intent", "terminal_applied"}:
        raise ValueError("Semantic event required")
    if type(ticket_key) is not bytes or len(ticket_key) != 32:
        raise ValueError("Host ticket key must contain exactly 32 bytes")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        parsed = inspect_authenticated_controller_event(
            session, fence, peer, wire, expected_process_profile=PROFILE
        )
    prefix = f"oe-event:{event.epoch_id}:{event.kind}:{parsed.payload.iteration}"
    event_object = publish_search_object(
        factory,
        fence,
        kind="search_controller_event",
        content=event.raw_event,
        logical_key=prefix + ":event",
        settings=settings,
    )
    references = {}
    for projection in evidence_projections(parsed):
        references[projection.kind] = _ref(
            publish_search_object(
                factory,
                fence,
                kind=projection.kind,
                content=projection.content,
                logical_key=prefix + ":" + projection.kind,
                settings=settings,
            )
        )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        # Publication can outlive a controller. It is evidence, not permission
        # to mutate a phase after that controller has exited or lost its fence.
        current = inspect_authenticated_controller_event(
            session, fence, peer, wire, expected_process_profile=PROFILE
        )
        if isinstance(current.payload, SamplingPayload):
            parent = session.scalar(
                select(m.SearchCandidate).where(
                    m.SearchCandidate.epoch_id == event.epoch_id,
                    m.SearchCandidate.upstream_program_id == current.payload.parent_id,
                )
            )
            if parent is None:
                raise ValueError("Observed native parent has no retained candidate")
            _owned_row(session, m.SearchCandidate, parent.id, fence)
            previous = session.scalar(
                select(m.SearchIterationIntent).where(
                    m.SearchIterationIntent.epoch_id == event.epoch_id,
                    m.SearchIterationIntent.native_iteration
                    == current.payload.iteration,
                )
            )
            if previous is None:
                reservation = reserve_budget(
                    session,
                    fence,
                    purpose="engine_mutation",
                    logical_key=f"oe-mutation:{event.epoch_id}:{current.payload.iteration}",
                    amounts=BudgetVector(mutation_slots=1),
                )
                reservation_id = reservation.id
            else:
                _owned_row(session, m.SearchIterationIntent, previous.id, fence)
                reservation_id = previous.mutation_reservation_id
            command = sampling_command(
                current,
                parent_candidate_id=parent.id,
                parent_source_sha256=parent.source_sha256,
                mutation_reservation_id=reservation_id,
                selection_ref=references["search_context"],
                state_ref=references["search_population"],
            )
        else:
            ack_id = outcome_id = None
            if current.payload.iteration == 0:
                seed = session.scalar(
                    select(m.SearchCandidate).where(
                        m.SearchCandidate.epoch_id == event.epoch_id,
                        m.SearchCandidate.kind == "seed",
                    )
                )
                if seed is None:
                    raise ValueError("Seed terminal lacks retained seed")
                ack = session.scalar(
                    select(m.SearchObjectiveAcknowledgement).where(
                        m.SearchObjectiveAcknowledgement.epoch_id == event.epoch_id,
                        m.SearchObjectiveAcknowledgement.candidate_id == seed.id,
                    )
                )
                if ack is None:
                    raise ValueError("Seed terminal lacks objective acknowledgement")
                ack_id = ack.id
            else:
                outcome = session.scalar(
                    select(m.SearchWorkerOutcome).where(
                        m.SearchWorkerOutcome.epoch_id == event.epoch_id,
                        m.SearchWorkerOutcome.native_iteration
                        == current.payload.iteration,
                    )
                )
                if outcome is None:
                    raise ValueError("Terminal lacks retained worker outcome")
                outcome_id = outcome.id
            command = terminal_command(
                current,
                state_ref=references["search_population"],
                worker_outcome_id=outcome_id,
                seed_objective_ack_id=ack_id,
            )
        receipt = record_controller_event(
            session,
            fence,
            peer,
            wire,
            expected_process_profile=PROFILE,
            event_reference=_ref(event_object),
            command=command,
            settings=settings,
        )
        if isinstance(current.payload, SamplingPayload):
            intent = _owned_row(
                session, m.SearchIterationIntent, receipt.intent_id, fence
            )
            ticket = _ticket(ticket_key, event.epoch_id, intent.id)
            register_worker_ticket(
                session,
                fence,
                intent_id=intent.id,
                controller_receipt_id=receipt.id,
                ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
            )
            grant = IterationGrant(
                schema_version="mineagent.openevolve-iteration-grant.v1",
                admission_id=event.admission_id,
                epoch_id=event.epoch_id,
                intent_id=intent.id,
                intent_ticket=ticket,
                native_iteration=intent.native_iteration,
                parent_candidate_id=intent.parent_candidate_id,
                parent_source_sha256=intent.parent_source_sha256,
                selection_sha256=intent.selection_sha256,
            )
            answer = {
                "iteration_grant": grant.model_dump(mode="json"),
                "sampling_receipt_id": str(receipt.control_id),
                "control_sequence": receipt.control_sequence,
                "state_sha256": receipt.control_state_sha256,
            }
        else:
            answer = {
                "frontier": current.payload.iteration,
                "state_sha256": receipt.control_state_sha256,
            }
        receipt_id = receipt.id
    return receipt_id, answer


def prepare_controller_checkpoint(factory, fence, peer, *, event):
    if type(event) is not Event or event.kind != "checkpoint_prepare":
        raise ValueError("Exact checkpoint prepare event required")
    _wire(event)
    payload = event.payload
    if (
        set(payload) != {"frontier", "state_sha256", "save_ordinal"}
        or type(payload["save_ordinal"]) is not int
        or not 1 <= payload["save_ordinal"] <= 4
        or type(payload["frontier"]) is not int
        or not 0 <= payload["frontier"] <= 2
    ):
        raise ValueError("Closed checkpoint preparation required")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _launch, epoch = require_search_controller(
            session, fence, peer, epoch_id=event.epoch_id
        )
        if epoch.admission_id != event.admission_id:
            raise ValueError("Checkpoint process differs")
        terminal = session.scalar(
            select(m.SearchTerminalApplication).where(
                m.SearchTerminalApplication.epoch_id == epoch.id,
                m.SearchTerminalApplication.frontier == payload["frontier"],
            )
        )
        if terminal is None:
            raise ValueError("Checkpoint frontier has no terminal receipt")
        intent = prepare_checkpoint(
            session,
            fence,
            CheckpointIntentCommand(
                save_ordinal=payload["save_ordinal"],
                epoch_id=epoch.id,
                terminal_id=terminal.id,
                frontier=payload["frontier"],
                state_sha256=payload["state_sha256"],
                request_sha256=event.event_sha256,
            ),
        )
        receipt_id = intent.id
        answer = {"token": str(intent.id), "path": f"/work/checkpoints/{intent.id}"}
    return receipt_id, answer
