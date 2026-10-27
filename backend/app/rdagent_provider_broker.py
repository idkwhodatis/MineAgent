"""Actual model-backed fenced broker; immutable replay never resubmits a call.

Only the operator supplies endpoint/secret reference/SDK Python. Generated source
is opaque response text here. SDK process evidence is persisted before its private
request pipe is written and after independently observed whole-group termination.
"""

import hashlib
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.rdagent_operational_broker import _authorize, _publish
from backend.app.rdagent_provider_profile import (
    resolve_provider_declaration,
    resolve_provider_runtime,
)
from backend.app.rdagent_provider_receipts import bind_receipt, read_chain
from backend.app.rdagent_services import (
    _completed_member_response,
    _scope,
    _scope_row,
    admit_atomic_request,
    require_research_spec,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
)
from backend.app.research_broker_services import mark_submitted, settle_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_services import read_research_object
from packages.rdagent_provider.accounting import expected_usage, request_reservation
from packages.rdagent_provider.codec import (
    canonical_bytes,
    parse_request,
    request_sha256,
)
from packages.rdagent_provider.contracts import (
    RDProviderChatResponse,
    RDProviderEmbeddingResponse,
    RDProviderUsage,
    require_request_binding,
)
from packages.rdagent_provider.process_receipts import (
    OBJECT_KIND,
    SDKIntent,
    SDKStarted,
    SDKStopped,
    receipt_bytes,
)
from packages.rdagent_provider.transport import ProviderFailure, execute
from packages.research_runtime.budget_contracts import BudgetVector


def _retain_receipt(factory, fence, settings, document, key):
    raw = receipt_bytes(document)
    object_id = _publish(
        factory, fence, settings, OBJECT_KIND, raw, key + ":" + document.kind
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        bind_receipt(
            session,
            fence,
            transport_id=document.transport_id,
            object_id=object_id,
            sha256=hashlib.sha256(raw).hexdigest(),
            settings=settings,
        )


def _settle(session, fence, transport_id, settings, *, response_id=None, response=None):
    transport = _scope_row(
        session, ResearchModelTransport, transport_id, fence, lock=True
    )
    operation = _scope_row(
        session, ResearchModelOperation, transport.operation_id, fence, lock=True
    )
    if transport.state != "submitted" or operation.state != "open":
        raise ValueError("SDK transport cannot be completed twice")
    _, config = require_research_spec(session, fence)
    request = parse_request(operation.protocol, operation.request)
    held = request_reservation(request, config.model_binding)
    reservation = _scope_row(
        session, ResearchBudgetReservation, transport.reservation_id, fence
    )
    if BudgetVector.model_validate(reservation.amounts) != held:
        raise ValueError("Provider physical reservation differs")
    read_chain(session, fence, transport_id, settings, completed=response is not None)
    response_sha = None
    if response is not None:
        response, _usage = expected_usage(
            operation.protocol, request, response, config.model_binding
        )
        raw = canonical_bytes(operation.protocol, response)
        response_sha = hashlib.sha256(raw).hexdigest()
        if (
            read_research_object(
                session, fence, response_id, "model_response", response_sha, settings
            )
            != raw
        ):
            raise ValueError("Actual retained provider response differs")
    elif response_id is not None:
        raise ValueError(
            "Unknown transport cannot retain a successful response reference"
        )
    settlement = settle_budget(
        session,
        fence,
        transport.reservation_id,
        charge=held,
        outcome="conservative_unknown",
    )
    state = "completed" if response is not None else "unknown"
    row = ResearchModelOutcome(
        id=uuid4(),
        **_scope(fence),
        transport_id=transport.id,
        outcome=state,
        usage=settlement.charge,
        response_object_id=response_id,
        response_object_kind=None if response_id is None else "model_response",
        response_sha256=response_sha,
    )
    session.add(row)
    operation.state = transport.state = state
    session.flush()
    return row


def submit_provider_request(
    factory,
    fence,
    *,
    call_id,
    idempotency_key,
    relation,
    protocol,
    request_bytes,
    settings,
    controller_peer,
    original_deadline,
):
    from backend.app.rdagent_models import FactorGenerationModelOperation

    if (
        type(call_id) is not UUID
        or type(idempotency_key) is not UUID
        or relation not in {"initial", "continuation", "semantic_repair"}
    ):
        raise ValueError("Exact provider request context required")
    request = parse_request(protocol, request_bytes)
    if canonical_bytes(protocol, request) != request_bytes:
        raise ValueError("Exact canonical provider request required")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        _authorize(controller_peer, session, fence)
        _, config = require_research_spec(session, fence)
        require_request_binding(request, config.model_binding)
        member = session.scalar(
            select(FactorGenerationModelOperation).filter_by(
                **_scope(fence), call_id=call_id, idempotency_key=idempotency_key
            )
        )
        if member is not None:
            operation = _scope_row(
                session, ResearchModelOperation, member.operation_id, fence
            )
            if (
                member.relation != relation
                or operation.protocol != protocol
                or operation.request_sha256 != request_sha256(protocol, request)
            ):
                raise ValueError("Immutable provider replay drift")
            _outcome, response = _completed_member_response(
                session, fence, member, settings
            )
            return operation.id, canonical_bytes(protocol, response)
        operator, binding = resolve_provider_declaration(
            settings, fence.owner_id, request.model_profile_id
        )
        if binding != config.model_binding:
            raise ValueError(
                "Current operator routing differs from frozen model binding"
            )
    resolve_provider_runtime(settings)
    sdk_python = str(settings.rdagent_provider_python)
    secret = os.environ.get(operator.connection.secret_env)
    if not secret:
        raise ProviderFailure("provider_secret_unavailable", contained=False)
    if type(original_deadline) not in (int, float) or not math.isfinite(
        original_deadline
    ):
        raise ValueError("Original host controller deadline required")
    if time.monotonic() >= original_deadline:
        raise ProviderFailure("original_controller_deadline", contained=False)
    key = f"rd-provider:{call_id.hex}:{idempotency_key.hex}"
    request_id = _publish(
        factory, fence, settings, "model_request", request_bytes, key + ":request"
    )
    with neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        _authorize(controller_peer, session, fence)
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
        operation_id, transport_id = operation.id, transport.id
        mark_submitted(session, fence, transport_id)
    now = time.monotonic_ns()
    if type(original_deadline) not in (int, float):
        raise ValueError("Original host controller deadline required")
    deadline_ns = min(
        int(original_deadline * 1_000_000_000),
        now + binding.request_deadline_ms * 1_000_000,
    )
    deadline = deadline_ns / 1_000_000_000
    if int(deadline * 1_000_000_000) > deadline_ns:
        deadline = math.nextafter(deadline, -math.inf)
    deadline_ns = int(deadline * 1_000_000_000)
    boot = UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    scope = dict(
        **_scope(fence),
        transport_id=transport_id,
        request_sha256=request_sha256(protocol, request),
        model_binding_sha256=binding.sha256,
    )
    intent = SDKIntent(
        **scope,
        admitted_monotonic_ns=now,
        deadline_monotonic_ns=deadline_ns,
        maximum_duration_ms=binding.request_deadline_ms,
        boot_id=boot,
    )
    _retain_receipt(factory, fence, settings, intent, key)

    def identity_boundary(event, evidence):
        if event == "started":
            # This transaction and retained publication must finish before execute
            # releases ANY request/secret bytes into the private SDK pipe.
            with neutral_transaction(
                factory, fence, resource_scope="objects"
            ) as session:
                _authorize(controller_peer, session, fence)
                current, current_binding = resolve_provider_declaration(
                    settings, fence.owner_id, request.model_profile_id
                )
                if current_binding != binding or current != operator:
                    raise ValueError("Provider routing changed before SDK submission")
            document = SDKStarted(**scope, identity=asdict(evidence))
        elif event == "stopped":
            document = SDKStopped(**scope, **asdict(evidence))
        else:
            raise ValueError("Unknown SDK physical boundary")
        if (
            document.identity.boot_id != intent.boot_id
            or document.identity.deadline_monotonic_ns != intent.deadline_monotonic_ns
        ):
            raise ValueError("Observed SDK identity differs from original intent")
        _retain_receipt(factory, fence, settings, document, key)
        if event == "started":
            with neutral_transaction(
                factory, fence, resource_scope="objects"
            ) as session:
                _authorize(controller_peer, session, fence)

    try:
        result = execute(
            request,
            binding=binding,
            base_url=operator.connection.base_url,
            secret=secret,
            deadline_monotonic=deadline,
            identity_boundary=identity_boundary,
            python_executable=sdk_python,
        )
        usage = RDProviderUsage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            provider_request_id=result.provider_request_id,
            usage_source=result.usage_source,
            billing_status=result.billing_status,
        )
        response = (
            RDProviderChatResponse(
                content=result.content, finish_reason=result.finish_reason, usage=usage
            )
            if request.kind == "chat"
            else RDProviderEmbeddingResponse(vectors=result.vectors, usage=usage)
        )
        response, _ = expected_usage(protocol, request, response, binding)
        response_id = _publish(
            factory,
            fence,
            settings,
            "model_response",
            canonical_bytes(protocol, response),
            key + ":response",
        )
        with neutral_transaction(factory, fence, resource_scope="objects") as session:
            _settle(
                session,
                fence,
                transport_id,
                settings,
                response_id=response_id,
                response=response,
            )
        return operation_id, canonical_bytes(protocol, response)
    except BaseException as original:
        # Missing physical observations or lost fence must stay reserved/uncertain.
        # Do not fabricate a never-launched receipt or retry a possibly sent call.
        try:
            with neutral_transaction(
                factory, fence, resource_scope="objects"
            ) as session:
                _settle(session, fence, transport_id, settings)
        except Exception:  # noqa: BLE001 - preserve original failure and conservative reservation
            # No successful receipt is returned; unresolved physical/DB state stays held.
            original.add_note(
                "Provider settlement remains uncertain; reservation retained"
            )
        raise
