"""Fixed-fixture actual retained request/transport/response accounting for 024.

No provider callback, network provider, fabricated successful model outcome, or
placeholder request. Live exact assigned worker is required before submission.
True response persistence and settlement after submission are fence-only, so a
controller/worker exit does not erase a completed physical fixture exchange.
"""

import hashlib
from uuid import uuid4

from sqlalchemy import select

from backend.app.openevolve_models import SearchRenderedContext
from backend.app.openevolve_services import _scope
from backend.app.openevolve_worker_actions import (
    _open_worker,
    require_worker_model_operation,
)
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
)
from backend.app.research_broker_services import (
    mark_submitted,
    reserve_transport,
    settle_transport,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_services import (
    read_research_object,
)
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.persisted_broker import PROTOCOL
from packages.research_runtime.broker_contracts import ObjectRef
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    canonical_bytes,
    parse_request,
    validate_response,
)
from runtimes.openevolve_operational.profile import PROFILE

ACCOUNTING = "mineagent.openevolve-fixture-byte-units.v1"


def fixture_usage(request_bytes, response):
    request = parse_request(PROTOCOL, request_bytes)
    if canonical_bytes(PROTOCOL, request) != request_bytes:
        raise ValueError("exact immutable OE request bytes")
    response = validate_response(PROTOCOL, request, response)
    prompt = sum(len(m.content.encode()) for m in request.messages)
    completion = (len(response.content.encode()) + 127) // 128
    if completion > request.max_output_tokens:
        raise ValueError("fixture completion upper bound")
    return (
        response,
        BudgetVector(calls=1, tokens=prompt + completion),
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    )


def _publish(factory, fence, settings, kind, raw, key):
    from backend.app.openevolve_object_publication import publish_search_object

    if kind not in {"model_request", "model_response"}:
        raise ValueError("closed model evidence")
    return publish_search_object(
        factory, fence, kind=kind, content=raw, logical_key=key, settings=settings
    )


def _operation(session, fence, grant, request_bytes):
    rendered = session.scalar(
        select(SearchRenderedContext).filter_by(intent_id=grant.intent_id)
    )
    if rendered is None or any(
        getattr(rendered, k) != v for k, v in _scope(fence).items()
    ):
        raise ValueError("exact rendered context required")
    if hashlib.sha256(request_bytes).hexdigest() != rendered.request_sha256:
        raise ValueError("rendered request drift")
    operation = session.scalar(
        select(ResearchModelOperation).filter_by(
            context_id=rendered.context_id, purpose="chat", ordinal=0
        )
    )
    if operation is not None and (
        any(getattr(operation, k) != v for k, v in _scope(fence).items())
        or operation.protocol != PROTOCOL
        or operation.request_sha256 != rendered.request_sha256
        or canonical(operation.request) != request_bytes
    ):
        raise ValueError("immutable operation replay drift")
    return rendered, operation


def _completed(
    session, fence, operation, request_bytes, response_bytes, usage, settings
):
    if operation is None:
        return None
    if operation.state != "completed":
        raise ValueError(
            "incomplete request requires recovery, never silent resubmission"
        )
    transports = session.scalars(
        select(ResearchModelTransport)
        .filter_by(operation_id=operation.id)
        .order_by(ResearchModelTransport.ordinal)
    ).all()
    # First operational fixture does not schedule transport retries. No second
    # physical call is inferred from a lost ACK or an incomplete operation.
    if (
        len(transports) != 1
        or transports[0].ordinal != 1
        or transports[0].state != "completed"
    ):
        raise ValueError("closed fixture transport sequence")
    transport = transports[0]
    outcome = session.scalar(
        select(ResearchModelOutcome).filter_by(transport_id=transport.id)
    )
    if (
        outcome is None
        or outcome.outcome != "completed"
        or outcome.usage != usage.model_dump()
        or any(
            getattr(row, k) != v
            for row in (transport, outcome)
            for k, v in _scope(fence).items()
        )
    ):
        raise ValueError("fixture completed accounting differs")
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
        or read_research_object(
            session,
            fence,
            outcome.response_object_id,
            "model_response",
            outcome.response_sha256,
            settings,
        )
        != response_bytes
    ):
        raise ValueError("retained fixture bytes differ")
    return outcome.id


def submit_fixture_request(
    factory, fence, peer, grant, *, request_bytes, fixture_response, settings
):
    response, usage, wire_usage = fixture_usage(request_bytes, fixture_response)
    response_bytes = canonical_bytes(PROTOCOL, response)
    request = parse_request(PROTOCOL, request_bytes)
    with neutral_transaction(factory, fence) as session:
        _open_worker(session, fence, peer, grant, PROFILE)
        rendered, existing = _operation(session, fence, grant, request_bytes)
        replay = _completed(
            session, fence, existing, request_bytes, response_bytes, usage, settings
        )
        if replay is not None:
            return replay, response.content, wire_usage
    request_obj = _publish(
        factory,
        fence,
        settings,
        "model_request",
        request_bytes,
        f"oe-request:{grant.intent_id}",
    )
    with neutral_transaction(factory, fence) as session:
        _open_worker(session, fence, peer, grant, PROFILE)
        rendered, existing = _operation(session, fence, grant, request_bytes)
        if existing is not None:
            replay = _completed(
                session, fence, existing, request_bytes, response_bytes, usage, settings
            )
            return replay, response.content, wire_usage
        operation = ResearchModelOperation(
            id=uuid4(),
            **_scope(fence),
            context_id=rendered.context_id,
            protocol=PROTOCOL,
            purpose="chat",
            ordinal=0,
            request=request.model_dump(mode="json"),
            request_sha256=rendered.request_sha256,
            request_object_id=request_obj.id,
            request_object_kind="model_request",
            state="open",
        )
        session.add(operation)
        session.flush()
        require_worker_model_operation(
            session,
            fence,
            peer,
            grant,
            expected_process_profile=PROFILE,
            context_id=rendered.context_id,
            operation_id=operation.id,
            request_sha256=operation.request_sha256,
        )
        transport = reserve_transport(
            session, fence, operation.id, operation.request_sha256
        )
        operation_id, transport_id, context_id = (
            operation.id,
            transport.id,
            rendered.context_id,
        )
    with neutral_transaction(factory, fence) as session:
        # Last current live worker/phase check directly before physical submission.
        require_worker_model_operation(
            session,
            fence,
            peer,
            grant,
            expected_process_profile=PROFILE,
            context_id=context_id,
            operation_id=operation_id,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        )
        mark_submitted(session, fence, transport_id)
    # Host-owned maintained schedule is the fixture provider. There is no network
    # request/provider callable and no token/pricing claim beyond ACCOUNTING.
    response_obj = _publish(
        factory,
        fence,
        settings,
        "model_response",
        response_bytes,
        f"oe-response:{operation_id}",
    )
    with neutral_transaction(factory, fence) as session:
        outcome = settle_transport(
            session,
            fence,
            transport_id,
            outcome="completed",
            usage=usage,
            response_object=ObjectRef(
                id=response_obj.id, kind="model_response", sha256=response_obj.sha256
            ),
            response=response,
        )
        receipt = outcome.id
    return receipt, response.content, wire_usage
