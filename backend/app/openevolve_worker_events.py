"""Protected worker prompt/proposal handlers using actual retained evidence.

No arbitrary callback/handler registry and no model or evaluator launch. These
closed handlers revalidate the assigned kernel peer in every mutation transaction.
Publication interrupted by peer death is conservatively retained, not adopted.
"""

import hashlib
import hmac
from uuid import uuid4

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app.openevolve_event_identity import exact_event, require_event_ticket
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_services import _owned_row, _scope
from backend.app.openevolve_worker_actions import (
    _open_worker,
    apply_worker_observer_command,
)
from backend.app.openevolve_worker_services import bind_started_worker
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
    ResearchOperationContext,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    ProposalResponseCommand,
    ProposalSourceCommand,
    ProposalSourceEnvelope,
    RenderedContextCommand,
    RenderedProjection,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from packages.research_runtime.broker_contracts import digest
from runtimes.openevolve_operational.profile import PROFILE
from runtimes.openevolve_operational.wire import Event


def context_ticket(key, context):
    if (
        type(key) is not bytes
        or len(key) != 32
        or type(context) is not SearchOperationContext
    ):
        raise ValueError("Fixed host context key and typed context required")
    return hmac.new(
        key, b"oe-context-v1:" + digest(context).encode(), hashlib.sha256
    ).hexdigest()


def _grant(event, keys):
    if type(event) is not Event or set(event.payload) != set(keys) | {"grant"}:
        raise ValueError("Closed worker event required")
    exact_event(event)
    grant = IterationGrant.model_validate_json(canonical(event.payload["grant"]))
    if (grant.admission_id, grant.epoch_id) != (event.admission_id, event.epoch_id):
        raise ValueError("Worker event grant scope differs")
    return grant


def _apply(session, fence, peer, grant, command, event, settings):
    return apply_worker_observer_command(
        session,
        fence,
        peer,
        grant,
        command,
        event=event,
        expected_process_profile=PROFILE,
        settings=settings,
    )


def start_worker(factory, fence, peer, *, event):
    if event.kind != "worker_started":
        raise ValueError("Worker start required")
    grant = _grant(event, ())
    with neutral_transaction(factory, fence) as session:
        require_event_ticket(session, fence, event)
        row = bind_started_worker(
            session, fence, peer, grant, expected_process_profile=PROFILE
        )
        receipt = row.id
    return receipt, {"started": True}


def render_prompt(factory, fence, peer, *, event, ticket_key, settings):
    if event.kind != "rendered_prompt":
        raise ValueError("Rendered prompt required")
    grant = _grant(event, ("projection", "rendered_sha256"))
    projection = RenderedProjection.model_validate_json(
        canonical(event.payload["projection"])
    )
    raw = canonical(projection.model_dump(mode="json"))
    sha = hashlib.sha256(raw).hexdigest()
    if sha != event.payload["rendered_sha256"]:
        raise ValueError("Actual rendered projection hash differs")
    context = SearchOperationContext(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        epoch_id=grant.epoch_id,
        iteration_intent_id=grant.intent_id,
        native_iteration=grant.native_iteration,
        operation_ordinal=0,
        parent_candidate_id=grant.parent_candidate_id,
        parent_source_sha256=grant.parent_source_sha256,
        selection_snapshot_sha256=grant.selection_sha256,
        rendered_context_sha256=sha,
    )
    ticket = context_ticket(ticket_key, context)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_rendered_prompt",
        content=raw,
        logical_key=f"oe-rendered:{grant.intent_id}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
        row = session.scalar(
            select(ResearchOperationContext).filter_by(
                **_scope(fence), context_sha256=digest(context)
            )
        )
        if row is None:
            row = ResearchOperationContext(
                id=uuid4(),
                **_scope(fence),
                context=context.model_dump(mode="json"),
                context_sha256=digest(context),
            )
            session.add(row)
            session.flush()
        elif row.context != context.model_dump(mode="json"):
            raise ValueError("Immutable context hash collision/drift")
        result = _apply(
            session,
            fence,
            peer,
            grant,
            RenderedContextCommand(
                epoch_id=grant.epoch_id,
                intent_id=grant.intent_id,
                context_id=row.id,
                projection=EvidenceRef(id=obj.id, sha256=obj.sha256),
            ),
            "rendered_prompt",
            settings,
        )
        receipt = result.id
    return receipt, {
        "context": context.model_dump(mode="json"),
        "context_ticket": ticket,
    }


def observe_proposal_response(factory, fence, peer, *, event, settings):
    if event.kind != "proposal_response":
        raise ValueError("Proposal response required")
    grant = _grant(event, ("response",))
    response = event.payload["response"]
    if type(response) is not str or not 0 < len(response) <= 32768:
        raise ValueError("Actual bounded completed response required")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
        rendered = session.scalar(
            select(m.SearchRenderedContext).where(
                m.SearchRenderedContext.intent_id == grant.intent_id
            )
        )
        if rendered is None:
            raise ValueError("Proposal lacks rendered context")
        operation = session.scalar(
            select(ResearchModelOperation).where(
                ResearchModelOperation.context_id == rendered.context_id,
                ResearchModelOperation.purpose == "chat",
                ResearchModelOperation.ordinal == 0,
            )
        )
        if (
            operation is None
            or operation.state != "completed"
            or operation.protocol != "mineagent.openevolve-chat.v1"
        ):
            raise ValueError("Proposal lacks exact completed operation")
        _owned_row(session, ResearchModelOperation, operation.id, fence)
        transports = list(
            session.scalars(
                select(ResearchModelTransport)
                .where(ResearchModelTransport.operation_id == operation.id)
                .limit(2)
            ).all()
        )
        if (
            len(transports) != 1
            or transports[0].ordinal != 1
            or transports[0].state != "completed"
        ):
            raise ValueError("Fixed fixture transport chain differs")
        outcome = session.scalar(
            select(ResearchModelOutcome).where(
                ResearchModelOutcome.transport_id == transports[0].id
            )
        )
        if outcome is None or outcome.outcome != "completed":
            raise ValueError("Proposal lacks completed transport outcome")
        row = _apply(
            session,
            fence,
            peer,
            grant,
            ProposalResponseCommand(
                epoch_id=grant.epoch_id,
                intent_id=grant.intent_id,
                model_outcome_id=outcome.id,
                response_sha256=outcome.response_sha256,
                response_text=response,
            ),
            "proposal_response",
            settings,
        )
        receipt = row.id
    return receipt, {"recorded": True}


def observe_proposal_source(factory, fence, peer, *, event, settings):
    if event.kind != "proposal_source":
        raise ValueError("Proposal source required")
    grant = _grant(
        event, ("stage", "source", "source_sha256", "source_bytes", "evidence")
    )
    value = ProposalSourceEnvelope.model_validate_json(
        canonical({k: v for k, v in event.payload.items() if k != "grant"})
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_proposal_source",
        content=canonical(value.model_dump(mode="json")),
        logical_key=f"oe-proposal:{grant.intent_id}:{value.stage}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
        proposal = session.scalar(
            select(m.SearchProposalResponse).where(
                m.SearchProposalResponse.intent_id == grant.intent_id
            )
        )
        if proposal is None:
            raise ValueError("Applied proposal lacks actual response")
        row = _apply(
            session,
            fence,
            peer,
            grant,
            ProposalSourceCommand(
                epoch_id=grant.epoch_id,
                intent_id=grant.intent_id,
                proposal_id=proposal.id,
                source=EvidenceRef(id=obj.id, sha256=obj.sha256),
            ),
            "proposal_" + value.stage,
            settings,
        )
        receipt = row.id
    return receipt, {"recorded": True}


def finish_worker(factory, fence, peer, *, event, settings):
    """Retain actual returned native result before committing worker closure."""
    from packages.openevolve_adapter.host_contracts import WorkerOutcomeCommand
    from packages.openevolve_adapter.worker_outcome_evidence import SCHEMA, validate

    if event.kind != "worker_outcome":
        raise ValueError("Actual worker result required")
    grant = _grant(
        event, ("iteration", "outcome", "child", "parent_id", "target_island")
    )
    observation = {k: v for k, v in event.payload.items() if k != "grant"}
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        _open_worker(session, fence, peer, grant, PROFILE)
        candidate = session.scalar(
            select(m.SearchCandidateGrant).where(
                m.SearchCandidateGrant.intent_id == grant.intent_id
            )
        )
        ack = (
            None
            if candidate is None
            else session.scalar(
                select(m.SearchObjectiveAcknowledgement).where(
                    m.SearchObjectiveAcknowledgement.grant_id == candidate.id
                )
            )
        )
        if ack is not None:
            _owned_row(session, m.SearchObjectiveAcknowledgement, ack.id, fence)
        projection = {
            "epoch_id": str(grant.epoch_id),
            "intent_id": str(grant.intent_id),
            "native_iteration": grant.native_iteration,
            "status": observation["outcome"],
            "objective_ack_id": None if ack is None else str(ack.id),
        }
    document = validate(
        {
            "schema_version": SCHEMA,
            "projection": projection,
            "observation": observation,
            "event_sha256": event.event_sha256,
        }
    )
    obj = publish_search_object(
        factory,
        fence,
        kind="search_worker_outcome",
        content=canonical(document),
        logical_key=f"oe-worker-outcome:{grant.intent_id}",
        settings=settings,
    )
    command = WorkerOutcomeCommand.model_validate_json(
        canonical(projection | {"evidence": {"id": str(obj.id), "sha256": obj.sha256}})
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_event_ticket(session, fence, event)
        row = _apply(session, fence, peer, grant, command, "worker_outcome", settings)
        receipt = row.id
    return receipt, {"recorded": True}
