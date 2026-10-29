"""Normal027 socket-to-durable service bridge, never caller-supplied peer authority.

Expected workers come from committed independent placement. For evaluator/broker
messages the host derives the original worker ticket only to select that exact
expected identity; the actual socket/kernel and separate context/evaluation
commitment must ALSO pass. No request can nominate a PID or refresh a deadline.
"""

import hmac
from typing import Literal
from uuid import UUID

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_channel_auth import authenticate_channel
from backend.app.oe_structured_controller_context import scoped
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_event_wire import reread_event
from backend.app.oe_structured_events import context_ticket
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
    OEStructuredProviderExchange,
)
from backend.app.oe_structured_provider_broker import submit_provider_chat
from backend.app.oe_structured_provider_evidence import read_completed_response
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.oe_structured_sampling_services import worker_ticket
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import Closed
from packages.openevolve_structured.contracts import (
    SHA,
    EvaluationGrant,
    SearchOperationContext,
)
from packages.openevolve_structured.host_events import WorkerDescriptor
from packages.openevolve_structured.provider_contracts import OEChatRequest


class ProviderSocketCall(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-broker-call.v1"]
    admission_id: UUID
    process_ticket: SHA
    context_ticket: SHA
    context: SearchOperationContext
    request: OEChatRequest
    request_sha256: SHA


_CONTROLLER = frozenset(
    {
        "seed_source",
        "iteration_intent",
        "worker_placement",
        "terminal_applied",
        "checkpoint_prepare",
        "checkpoint_publish",
        "checkpoint_upload_begin",
        "checkpoint_upload_chunk",
        "checkpoint_upload_finish",
    }
)
_DESCRIPTOR = frozenset(
    {
        "worker_started",
        "rendered_prompt",
        "proposal_response",
        "proposal_source",
        "worker_outcome",
    }
)
_EVALUATION = frozenset({"execute_evaluation", "evaluation_complete"})


def _actor(session, fence, event, ticket_key):
    if event.kind in _CONTROLLER:
        return None, None
    supplied = event.payload.get("grant")
    if event.kind in _DESCRIPTOR or (
        event.kind == "candidate_intent" and supplied is not None
    ):
        if type(supplied) is not dict:
            fail("oe_structured_socket_descriptor")
        descriptor = WorkerDescriptor.model_validate_json(canonical_bytes(supplied))
        if (descriptor.admission_id, descriptor.epoch_id) != (
            event.admission_id,
            event.epoch_id,
        ):
            fail("oe_structured_socket_descriptor")
        return descriptor.intent_id, descriptor.intent_ticket
    if event.kind == "candidate_intent" and supplied is None:
        return None, None
    if event.kind in _EVALUATION:
        if type(supplied) is not dict:
            fail("oe_structured_socket_evaluation")
        grant = EvaluationGrant.model_validate_json(canonical_bytes(supplied))
        candidate = scoped(
            session.get(OEStructuredCandidate, grant.candidate_id), fence
        )
        execution = scoped(
            session.get(OEStructuredExecution, grant.execution_id), fence
        )
        if (
            candidate.epoch_id != event.epoch_id
            or grant.epoch_id != event.epoch_id
            or (grant.owner_id, grant.run_id) != (fence.owner_id, fence.run_id)
            or execution.candidate_id != candidate.id
            or candidate.upstream_program_id != grant.upstream_program_id
            or candidate.candidate_sha256 != grant.candidate_sha256
            or candidate.program_sha256 != grant.program_sha256
        ):
            fail("oe_structured_socket_evaluation")
        if candidate.ordinal == 0:
            return None, None
        exchange = scoped(
            session.get(OEStructuredProviderExchange, candidate.provider_exchange_id),
            fence,
        )
        if exchange.epoch_id != event.epoch_id:
            fail("oe_structured_socket_evaluation")
        return exchange.iteration_intent_id, worker_ticket(
            ticket_key, event.epoch_id, exchange.iteration_intent_id
        )
    fail("oe_structured_socket_event")


def authenticate_event(
    factory, fence, event, settings, channel, *, running, ticket_key
):
    event = reread_event(event)
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            sampling_id, token = _actor(session, fence, event, ticket_key)
            peer = authenticate_channel(
                session,
                fence,
                event.epoch_id,
                settings,
                channel,
                running=running,
                process_ticket=event.process_ticket,
                sampling_id=sampling_id,
                worker_ticket=token,
            )
        return peer


def _bound_call(session, fence, value, settings, peer, ticket_key):
    bound = require_provider_operation(
        session, fence, value.context.iteration_intent_id, settings, peer=peer
    )
    if (
        value.context != bound.context
        or value.request != bound.request
        or value.request_sha256 != bound.request.sha256
        or value.admission_id != peer.admission.id
        or not hmac.compare_digest(
            value.context_ticket, context_ticket(ticket_key, bound.context)
        )
    ):
        fail("oe_structured_socket_request")
    return bound


def handle_provider_socket(
    factory, fence, raw, settings, channel, *, running, ticket_key
):
    if type(raw) is not bytes or not 0 < len(raw) <= 524288:
        fail("oe_structured_socket_bound")
    value = ProviderSocketCall.model_validate_json(raw)
    if canonical_bytes(value) != raw:
        fail("oe_structured_socket_canonical")
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with (
            native_storage_guard(settings.storage_root),
            neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session,
        ):
            token = worker_ticket(
                ticket_key, value.context.epoch_id, value.context.iteration_intent_id
            )
            peer = authenticate_channel(
                session,
                fence,
                value.context.epoch_id,
                settings,
                channel,
                running=running,
                process_ticket=value.process_ticket,
                sampling_id=value.context.iteration_intent_id,
                worker_ticket=token,
            )
            bound = _bound_call(session, fence, value, settings, peer, ticket_key)
        # Broker owns its native guard and short transactions. Never nest them.
        exchange_id, response_raw = submit_provider_chat(
            factory,
            fence,
            bound.intent.id,
            canonical_bytes(bound.request),
            settings,
            peer=peer,
        )
        with (
            native_storage_guard(settings.storage_root),
            neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session,
        ):
            current = _bound_call(session, fence, value, settings, peer, ticket_key)
            if (
                current.intent.id != bound.intent.id
                or current.config != bound.config
                or current.context != bound.context
                or current.request != bound.request
            ):
                fail("oe_structured_socket_request_drift")
            exchange = scoped(
                session.get(OEStructuredProviderExchange, exchange_id), fence
            )
            if exchange.iteration_intent_id != bound.intent.id:
                fail("oe_structured_socket_response")
            request, response = read_completed_response(
                session, fence, exchange, bound.config, settings
            )
            if request != bound.request or canonical_bytes(response) != response_raw:
                fail("oe_structured_socket_response")
            reply = canonical_bytes(
                {
                    "schema_version": "mineagent.oe-structured-provider-broker-result.v1",
                    "request_sha256": bound.request.sha256,
                    "context_sha256": digest(bound.context),
                    "receipt_id": str(exchange.id),
                    "response": response.model_dump(mode="json"),
                }
            )
            if len(reply) > 262144:
                fail("oe_structured_socket_reply_bound")
        return reply
