"""Closed operational event dispatcher; no dynamic handlers or public API.

The caller supplies a socket-authenticated private Peer. Every concrete handler
rechecks its persisted scope/phase/current fence. Responses are returned only
following the handler's committed receipt, never before durable acknowledgement.
"""

from uuid import UUID

from backend.app import openevolve_candidate_events as candidates
from backend.app import openevolve_controller_events as controller
from backend.app import openevolve_worker_events as workers
from backend.app.openevolve_checkpoint_publication import finalize_checkpoint
from backend.app.openevolve_controller_services import require_search_controller
from backend.app.openevolve_evaluation_execution import execute_candidate
from backend.app.openevolve_event_identity import exact_event, require_event_ticket
from backend.app.openevolve_placement_events import observe_placement
from backend.app.openevolve_upload_services import (
    accept_chunk,
    begin_upload,
    finish_upload,
)
from backend.app.research_lifecycle import neutral_transaction
from runtimes.openevolve_operational.peer import ControllerBinding, Peer, WorkerBinding
from runtimes.openevolve_operational.wire import UPLOADS

CONTROLLER_ONLY = (
    frozenset(
        {
            "iteration_intent",
            "terminal_applied",
            "checkpoint_prepare",
            "checkpoint_publish",
            "worker_placement",
            "seed_source",
        }
    )
    | UPLOADS
)
WORKER_ONLY = frozenset(
    {
        "worker_started",
        "worker_outcome",
        "rendered_prompt",
        "proposal_response",
        "proposal_source",
    }
)
SHARED = frozenset({"candidate_intent", "execute_evaluation", "evaluation_complete"})


def dispatch_event(factory, fence, peer, *, event, ticket_key, settings):
    exact_event(event)
    if type(peer) is not Peer or type(ticket_key) is not bytes or len(ticket_key) != 32:
        raise ValueError("Private actual socket peer and host key required")
    if event.kind in CONTROLLER_ONLY:
        if type(peer.binding) is not ControllerBinding:
            raise ValueError("Only protected controller may issue this event")
    elif event.kind in WORKER_ONLY:
        if type(peer.binding) is not WorkerBinding:
            raise ValueError("Only independently assigned worker may issue this event")
    elif event.kind not in SHARED:
        # Historical wire lists candidate_outcome, but pinned observer emits no
        # such event. No catch-all acknowledgement or mutation is permitted.
        raise ValueError("Event has no operational handler")
    args = {"event": event}
    if event.kind in {"iteration_intent", "terminal_applied"}:
        return controller.apply_controller_event(
            factory, fence, peer, **args, ticket_key=ticket_key, settings=settings
        )
    if event.kind == "checkpoint_prepare":
        return controller.prepare_controller_checkpoint(factory, fence, peer, **args)
    if event.kind == "checkpoint_publish":
        return finalize_checkpoint(factory, fence, peer, **args, settings=settings)
    if event.kind == "worker_placement":
        return observe_placement(factory, fence, peer, **args, settings=settings)
    if event.kind == "seed_source":
        return candidates.check_seed_source(factory, fence, peer, **args)
    if event.kind == "worker_started":
        return workers.start_worker(factory, fence, peer, **args)
    if event.kind == "rendered_prompt":
        return workers.render_prompt(
            factory, fence, peer, **args, ticket_key=ticket_key, settings=settings
        )
    if event.kind == "proposal_response":
        return workers.observe_proposal_response(
            factory, fence, peer, **args, settings=settings
        )
    if event.kind == "proposal_source":
        return workers.observe_proposal_source(
            factory, fence, peer, **args, settings=settings
        )
    if event.kind == "worker_outcome":
        return workers.finish_worker(factory, fence, peer, **args, settings=settings)
    if event.kind == "candidate_intent":
        return candidates.register_candidate(
            factory, fence, peer, **args, ticket_key=ticket_key, settings=settings
        )
    if event.kind == "execute_evaluation":
        return execute_candidate(
            factory, fence, peer, **args, ticket_key=ticket_key, settings=settings
        )
    if event.kind == "evaluation_complete":
        return candidates.acknowledge_evaluation(
            factory, fence, peer, **args, ticket_key=ticket_key, settings=settings
        )
    # Upload helpers independently revalidate controller in every fresh
    # transaction. Ticket/hash is checked once against this immutable event.
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        require_search_controller(session, fence, peer, epoch_id=event.epoch_id)
    method = {
        "checkpoint_upload_begin": begin_upload,
        "checkpoint_upload_chunk": accept_chunk,
        "checkpoint_upload_finish": finish_upload,
    }[event.kind]
    result = method(
        factory,
        fence,
        peer,
        epoch_id=event.epoch_id,
        payload=event.payload,
        settings=settings,
    )
    token = UUID(result["token"])
    if str(token) != result["token"]:
        raise ValueError("Committed upload token must be canonical")
    return token, result
