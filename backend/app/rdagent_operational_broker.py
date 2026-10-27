"""Closed fixture-only RD host orchestration, not a registered RPC/provider API.

The host supplies an immutable maintained response schedule and a live controller
resolver, never request-supplied authority or provider callbacks. Live authority
is required at admission and immediately before submission. After submission,
response persistence/accounting deliberately require only the current fence:
controller death must not discard a true completion or release its charges.

Publication failure leaves durable reservations/submitted state conservative.
An incomplete existing operation is NOT automatically resubmitted by this helper.
No paid-provider tokenizer, currency, controller execution, or stop claim is made.
"""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.rdagent_models import FactorGenerationModelOperation
from backend.app.rdagent_services import admit_atomic_request, require_research_spec
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
)
from backend.app.research_broker_services import (
    mark_submitted,
    reserve_budget,
    settle_transport,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    publish_reserved_object,
    read_research_object,
    reserve_object,
)
from packages.research_runtime.broker_contracts import ObjectRef
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    canonical_bytes,
    parse_request,
    validate_response,
)
from runtimes.rdagent_operational.peer import ControllerPeer, require_controller_peer

ACCOUNTING = "mineagent.rdagent-fixture-byte-units.v1"
PROTOCOLS = {"mineagent.rdagent-chat.v1", "mineagent.rdagent-embedding.v1"}


@dataclass(frozen=True)
class FixtureReceipt:
    operation_id: UUID
    member_id: UUID
    transport_id: UUID
    outcome_id: UUID
    response_object_id: UUID
    response_bytes: bytes
    accounting: str = ACCOUNTING


def fixture_usage(protocol, request_bytes, response):
    """Input UTF8 bytes; chat output one unit per started128 UTF8 bytes.

    Embedding output is fixed two-dimensional/cardinality-checked and has no
    separate output charge under this fixture contract, matching its reservation.
    """
    if protocol not in PROTOCOLS or type(request_bytes) is not bytes:
        raise ValueError("Exact RD fixture protocol and request bytes required")
    request = parse_request(protocol, request_bytes)
    if canonical_bytes(protocol, request) != request_bytes:
        raise ValueError("Noncanonical immutable request")
    response = validate_response(protocol, request, response)
    if request.kind == "chat":
        output = (len(response.content.encode("utf-8")) + 127) // 128
        if output > request.max_output_tokens:
            raise ValueError("Fixture output exceeds reserved output units")
        tokens = sum(len(m.content.encode("utf-8")) for m in request.messages) + output
    else:
        tokens = sum(len(value.encode("utf-8")) for value in request.inputs)
    return response, BudgetVector(calls=1, tokens=tokens)


def _scope(fence):
    return {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }


def _authorize(callback, session, fence):
    # Callback must resolve persisted launch binding and return real issued peer;
    # a boolean, caller UUID, or fabricated dataclass is never enough.
    peer = callback(session, fence)
    if type(peer) is not ControllerPeer:
        raise ValueError("Host resolver must return authenticated controller peer")
    require_controller_peer(
        peer, fence, logical_operation_id=peer.admission.logical_operation_id
    )
    require_research_spec(session, fence)


def _publish(factory, fence, settings, kind, content, key):
    sha = hashlib.sha256(content).hexdigest()
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        held = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=key,
            amounts=BudgetVector(storage_bytes=2 * len(content) + 8192),
        )
        previous = session.scalar(
            select(ResearchObjectPublication).filter_by(budget_reservation_id=held.id)
        )
        if previous is not None:
            if any(getattr(previous, k) != v for k, v in _scope(fence).items()) or (
                previous.object_kind,
                previous.sha256,
                previous.size_bytes,
            ) != (kind, sha, len(content)):
                raise ValueError("Publication replay drift")
            outcome = session.get(ResearchObjectOutcome, previous.id)
            if outcome is not None:
                row = session.get(ResearchObject, outcome.object_id)
                if (
                    row is None
                    or read_research_object(session, fence, row.id, kind, sha, settings)
                    != content
                ):
                    raise ValueError("Retained publication differs")
                return row.id
        publication = reserve_object(session, fence, kind, content, held.id, settings)
        publication_id = publication.id
    return publish_reserved_object(factory, fence, publication_id, content, settings)


def _replay(
    session,
    fence,
    *,
    call_id,
    idempotency_key,
    relation,
    protocol,
    request_bytes,
    response_bytes,
    usage,
    settings,
):
    member = session.scalar(
        select(FactorGenerationModelOperation).filter_by(
            **_scope(fence), call_id=call_id, idempotency_key=idempotency_key
        )
    )
    if member is None:
        return None
    operation = session.get(ResearchModelOperation, member.operation_id)
    if operation is None or any(
        getattr(operation, k) != v for k, v in _scope(fence).items()
    ):
        raise ValueError("Operation scope differs")
    if (
        member.relation != relation
        or operation.protocol != protocol
        or member.parse_failure_object_id is not None
    ):
        raise ValueError("Request replay differs")
    if (
        read_research_object(
            session,
            fence,
            operation.request_object_id,
            "model_request",
            operation.request_sha256,
            settings,
        )
        != request_bytes
    ):
        raise ValueError("Immutable request replay differs")
    transport = session.scalar(
        select(ResearchModelTransport).filter_by(
            **_scope(fence), operation_id=operation.id, ordinal=1
        )
    )
    if (
        operation.state != "completed"
        or transport is None
        or transport.state != "completed"
    ):
        raise ValueError(
            "Incomplete fixture request requires explicit recovery; never resubmit"
        )
    outcome = session.scalar(
        select(ResearchModelOutcome).filter_by(
            **_scope(fence), transport_id=transport.id
        )
    )
    if (
        outcome is None
        or outcome.outcome != "completed"
        or outcome.usage != usage.model_dump()
    ):
        raise ValueError("Completed fixture outcome missing")
    stored = read_research_object(
        session,
        fence,
        outcome.response_object_id,
        "model_response",
        outcome.response_sha256,
        settings,
    )
    if stored != response_bytes:
        raise ValueError("Host fixture response schedule drift")
    return FixtureReceipt(
        operation.id,
        member.id,
        transport.id,
        outcome.id,
        outcome.response_object_id,
        stored,
    )


def submit_fixture_request(
    factory,
    fence,
    *,
    call_id,
    idempotency_key,
    relation,
    protocol,
    request_bytes,
    fixture_response,
    settings,
    revalidate_controller,
):
    """Real service/filesystem composition; caller supplies host-owned schedule.

    Resolver(session,fence) must check persisted exact protected-controller binding
    and return ControllerPeer. It is called inside each execution-authorizing
    transaction. Internal object publication and completion are fence-only.
    """
    if (
        type(call_id) is not UUID
        or type(idempotency_key) is not UUID
        or relation not in {"initial", "continuation"}
        or not callable(revalidate_controller)
    ):
        raise ValueError("Exact fixture invocation required")
    response, usage = fixture_usage(protocol, request_bytes, fixture_response)
    response_bytes = canonical_bytes(protocol, response)
    with neutral_transaction(factory, fence) as session:
        _authorize(revalidate_controller, session, fence)
        existing = _replay(
            session,
            fence,
            call_id=call_id,
            idempotency_key=idempotency_key,
            relation=relation,
            protocol=protocol,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            usage=usage,
            settings=settings,
        )
        if existing is not None:
            return existing
    key = f"rd-fixture:{call_id.hex}:{idempotency_key.hex}"
    request_id = _publish(
        factory, fence, settings, "model_request", request_bytes, key + ":request"
    )
    with neutral_transaction(factory, fence) as session:
        _authorize(revalidate_controller, session, fence)
        operation, member, transport = admit_atomic_request(
            session,
            fence,
            call_id=call_id,
            idempotency_key=idempotency_key,
            relation=relation,
            request_bytes=request_bytes,
            request_object_id=request_id,
            settings=settings,
        )
        operation_id, member_id, transport_id = operation.id, member.id, transport.id
    with neutral_transaction(factory, fence) as session:
        _authorize(revalidate_controller, session, fence)
        mark_submitted(session, fence, transport_id)
    # Host-owned, deterministic fixture response is now delivered. No callback,
    # network, subprocess, source execution, or new controller grant occurs here.
    response_id = _publish(
        factory, fence, settings, "model_response", response_bytes, key + ":response"
    )
    with neutral_transaction(factory, fence) as session:
        outcome = settle_transport(
            session,
            fence,
            transport_id,
            outcome="completed",
            usage=usage,
            response=response,
            response_object=ObjectRef(
                id=response_id,
                kind="model_response",
                sha256=hashlib.sha256(response_bytes).hexdigest(),
            ),
        )
        return FixtureReceipt(
            operation_id,
            member_id,
            transport_id,
            outcome.id,
            response_id,
            response_bytes,
        )
