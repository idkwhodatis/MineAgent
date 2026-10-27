"""Closed fixture broker RPC; canonical request identity and fresh worker scope.

The response schedule is fixed host-owned test input, not a provider callback or
wire field. Production paid/provider registration remains absent.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from backend.app.openevolve_controller_events import _ticket
from backend.app.openevolve_models import SearchIterationIntent
from backend.app.openevolve_operational_broker import submit_fixture_request
from backend.app.openevolve_services import _owned_row
from backend.app.openevolve_worker_actions import _open_worker
from backend.app.openevolve_worker_events import context_ticket
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.openevolve_adapter.persisted_broker import PROTOCOL
from packages.openevolve_adapter.worker_binding_contracts import IterationGrant
from packages.research_runtime.engine_broker_contracts import (
    OEChatResponse,
    canonical_bytes,
    parse_request,
)
from runtimes.openevolve_operational.peer import Peer, WorkerBinding
from runtimes.openevolve_operational.profile import PROFILE
from runtimes.openevolve_operational.transport import unique


@dataclass(frozen=True)
class FixtureSchedule:
    responses: tuple[OEChatResponse, ...]

    def __post_init__(self):
        if type(self.responses) is not tuple or not 1 <= len(self.responses) <= 2:
            raise ValueError("One or two fixed fixture responses required")
        for response in self.responses:
            if type(response) is not OEChatResponse:
                raise ValueError("Exact typed fixture response required")
            OEChatResponse.model_validate_json(response.model_dump_json())


def dispatch_broker(factory, fence, peer, *, raw, ticket_key, schedule, settings):
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= 1048576
        or type(schedule) is not FixtureSchedule
    ):
        raise ValueError("Bounded fixed fixture broker request required")
    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Nonfinite broker JSON")
        ),
    )
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "admission_id",
            "process_ticket",
            "context_ticket",
            "context",
            "request",
            "request_sha256",
        }
        or value["schema_version"] != "mineagent.openevolve-broker-call.v1"
        or canonical(value) != raw
    ):
        raise ValueError("Exact canonical broker envelope required")
    if type(peer) is not Peer or type(peer.binding) is not WorkerBinding:
        raise ValueError("Only actual assigned worker can submit model request")
    admission_id = UUID(value["admission_id"])
    if str(admission_id) != value["admission_id"] or admission_id != peer.admission.id:
        raise ValueError("Broker admission differs")
    process_ticket = value["process_ticket"]
    if (
        type(process_ticket) is not str
        or len(process_ticket) != 64
        or not hmac.compare_digest(
            hashlib.sha256(process_ticket.encode()).hexdigest(),
            peer.admission.ipc.ticket_sha256,
        )
    ):
        raise ValueError("Broker process ticket differs")
    context = SearchOperationContext.model_validate_json(canonical(value["context"]))
    if (
        context.model_dump(mode="json") != value["context"]
        or type(value["context_ticket"]) is not str
        or not hmac.compare_digest(
            context_ticket(ticket_key, context), value["context_ticket"]
        )
    ):
        raise ValueError("Immutable host context ticket differs")
    request_bytes = canonical(value["request"])
    request = parse_request(PROTOCOL, request_bytes)
    if (
        canonical_bytes(PROTOCOL, request) != request_bytes
        or hashlib.sha256(request_bytes).hexdigest() != value["request_sha256"]
    ):
        raise ValueError("Immutable request digest differs")
    if (
        context.owner_id,
        context.run_id,
        context.epoch_id,
        context.iteration_intent_id,
    ) != (fence.owner_id, fence.run_id, peer.binding.epoch_id, peer.binding.intent_id):
        raise ValueError("Broker context scope differs")
    with neutral_transaction(factory, fence) as session:
        intent = _owned_row(
            session, SearchIterationIntent, context.iteration_intent_id, fence
        )
        grant = IterationGrant(
            schema_version="mineagent.openevolve-iteration-grant.v1",
            admission_id=admission_id,
            epoch_id=context.epoch_id,
            intent_id=intent.id,
            intent_ticket=_ticket(ticket_key, context.epoch_id, intent.id),
            native_iteration=intent.native_iteration,
            parent_candidate_id=intent.parent_candidate_id,
            parent_source_sha256=intent.parent_source_sha256,
            selection_sha256=intent.selection_sha256,
        )
        _open_worker(session, fence, peer, grant, PROFILE)
        if (
            context.native_iteration,
            context.parent_candidate_id,
            context.parent_source_sha256,
            context.selection_snapshot_sha256,
        ) != (
            intent.native_iteration,
            intent.parent_candidate_id,
            intent.parent_source_sha256,
            intent.selection_sha256,
        ):
            raise ValueError("Broker immutable iteration context differs")
        if not 1 <= intent.native_iteration <= len(schedule.responses):
            raise ValueError("No host-owned fixture response for this iteration")
        response = schedule.responses[intent.native_iteration - 1]
    receipt, content, usage = submit_fixture_request(
        factory,
        fence,
        peer,
        grant,
        request_bytes=request_bytes,
        fixture_response=response,
        settings=settings,
    )
    return canonical(
        {
            "schema_version": "mineagent.openevolve-broker-result.v1",
            "request_sha256": value["request_sha256"],
            "context_sha256": hashlib.sha256(canonical(value["context"])).hexdigest(),
            "receipt_id": str(receipt),
            "content": content,
            "usage": usage,
        }
    )
