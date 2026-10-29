"""Exact real receipt/response codecs with DB doubles; no physical/PG acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_evidence as service
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured import provider_receipts as receipt
from packages.openevolve_structured.provider_accounting import request_reservation
from packages.openevolve_structured.provider_contracts import (
    ChatPricing,
    OEChatResponse,
)
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
from packages.openevolve_structured.provider_response import build_request
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_contracts import declared
from tests.openevolve.test_structured_provider_receipts import chain


def fixture(monkeypatch):
    profile, _, domain, _, parent, rendered, context, _ = declared()
    profile = OEOperatorChatProfile.model_validate(
        {
            **profile.model_dump(mode="python"),
            "pricing": ChatPricing(
                status="operator_declared",
                input_microusd_per_million=1,
                output_microusd_per_million=2,
            ),
        }
    )
    binding = profile.public_binding(context.owner_id)
    request = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=parent,
        rendered=rendered,
    )
    response = OEChatResponse(
        request_sha256=request.sha256,
        content=parent.decode(),
        finish_reason="stop",
        input_tokens=7,
        output_tokens=11,
        total_tokens=18,
        usage_source="provider",
    )
    fence = NS(
        owner_id=context.owner_id,
        run_id=context.run_id,
        attempt_id=uuid4(),
        generation=1,
    )

    def row(**kw):
        return NS(**vars(fence), **kw)

    exchange = row(
        id=uuid4(),
        provider_transport_id=uuid4(),
        iteration_intent_id=context.iteration_intent_id,
        request_object_id=uuid4(),
        request_kind="oe_structured_provider_request",
        request_sha256=request.sha256,
        response_object_id=uuid4(),
        response_kind="oe_structured_provider_response",
        response_sha256=digest(response),
        context=context.model_dump(mode="json"),
        context_sha256=digest(context),
    )
    membership = row(
        id=exchange.provider_transport_id,
        iteration_intent_id=context.iteration_intent_id,
        transport_id=uuid4(),
        operation_id=uuid4(),
        request_sha256=request.sha256,
        model_binding_sha256=binding.sha256,
    )
    operation = row(
        id=membership.operation_id,
        context_id=uuid4(),
        state="completed",
        protocol="mineagent.oe-structured-provider-chat.v1",
        purpose="chat",
        ordinal=0,
        request=request.model_dump(mode="json"),
        request_object_id=exchange.request_object_id,
        request_object_kind=exchange.request_kind,
        request_sha256=request.sha256,
    )
    transport = row(
        id=membership.transport_id,
        operation_id=operation.id,
        reservation_id=uuid4(),
        ordinal=1,
        state="completed",
    )
    held = request_reservation(request, binding).model_dump()
    reservation = row(
        id=transport.reservation_id,
        state="settled",
        purpose="model_transport",
        logical_key="oe-structured-provider:" + str(context.iteration_intent_id),
        amounts=held,
    )
    settlement = row(
        id=uuid4(),
        reservation_id=reservation.id,
        charge=held,
        outcome="conservative_unknown",
    )
    outcome = row(
        id=uuid4(),
        transport_id=transport.id,
        outcome="completed",
        usage=held,
        response_object_id=exchange.response_object_id,
        response_object_kind=exchange.response_kind,
        response_sha256=exchange.response_sha256,
    )
    ctx = row(
        id=operation.context_id,
        context=context.model_dump(mode="json"),
        context_sha256=digest(context),
    )
    objects = {
        exchange.request_object_id: canonical_bytes(request),
        exchange.response_object_id: canonical_bytes(response),
    }
    receipts = []
    for document in chain():
        raw = document.model_dump(mode="json")
        raw.update(
            {k: str(v) if k != "generation" else v for k, v in vars(fence).items()}
        )
        raw.update(
            transport_id=str(transport.id),
            request_sha256=request.sha256,
            model_binding_sha256=binding.sha256,
        )
        if document.kind == "intent":
            raw["maximum_duration_ms"] = binding.request_deadline_ms
        document = receipt.parse_receipt(canonical_bytes(raw))
        oid = uuid4()
        objects[oid] = receipt.receipt_bytes(document)
        receipts.append(
            row(
                id=uuid4(),
                membership_id=membership.id,
                kind=document.kind,
                receipt_object_id=oid,
                receipt_object_kind=receipt.OBJECT_KIND,
                receipt_sha256=digest(document),
                size_bytes=len(objects[oid]),
            )
        )
    singles = {
        service.OEStructuredProviderTransport: membership,
        service.ResearchModelTransport: transport,
        service.ResearchModelOperation: operation,
        service.ResearchOperationContext: ctx,
        service.ResearchBudgetReservation: reservation,
    }
    multiples = {
        service.ResearchModelTransport: [transport],
        service.ResearchModelOutcome: [outcome],
        service.ResearchBudgetSettlement: [settlement],
        service.OEStructuredProviderReceipt: receipts,
    }

    class Session:
        def get(self, cls, key):
            value = singles[cls]
            assert value.id == key
            return value

        def scalar(self, stmt):
            return None

        def scalars(self, stmt):
            if str(stmt) == "SELECT version_num FROM alembic_version":
                return NS(all=lambda: ["028_oe_structured_search"])
            return NS(all=lambda: multiples[stmt.column_descriptions[0]["entity"]])

    monkeypatch.setattr(service, "require_neutral_transaction", lambda *a, **kw: None)

    def read(_s, _f, oid, kind, sha, _settings):
        import hashlib

        raw = objects[oid]
        assert hashlib.sha256(raw).hexdigest() == sha
        return raw

    monkeypatch.setattr(service, "read_research_object", read)
    return NS(
        session=Session(),
        fence=fence,
        exchange=exchange,
        config=NS(model_binding=binding),
        request=request,
        response=response,
        membership=membership,
        transport=transport,
        operation=operation,
        reservation=reservation,
        settlement=settlement,
        outcome=outcome,
        ctx=ctx,
        receipts=receipts,
        multiples=multiples,
    )


def read(case):
    return service.read_completed_response(
        case.session, case.fence, case.exchange, case.config, None
    )


def test_exact_new_protocol_scope_full_charge_and_actual_physical_receipt_bytes(
    monkeypatch,
):
    case = fixture(monkeypatch)
    assert read(case) == (case.request, case.response)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("transport", "state", "unknown"),
        ("operation", "protocol", "mineagent.rdagent-chat.v1"),
        ("membership", "request_sha256", "0" * 64),
        ("reservation", "state", "reserved"),
        ("settlement", "outcome", "not_submitted"),
        ("ctx", "context_sha256", "0" * 64),
        ("membership", "transport_id", uuid4()),
    ],
)
def test_no_cross_protocol_unknown_or_refunded_completion(
    monkeypatch, target, field, value
):
    case = fixture(monkeypatch)
    setattr(getattr(case, target), field, value)
    with pytest.raises((ProcessError, AssertionError)):
        read(case)


def test_hidden_second_physical_transport_rejected(monkeypatch):
    case = fixture(monkeypatch)
    case.multiples[service.ResearchModelTransport].append(NS(id=uuid4()))
    with pytest.raises(ProcessError):
        read(case)


def test_sdk_membership_id_is_not_substituted_for_physical_transport(monkeypatch):
    case = fixture(monkeypatch)
    case.membership.transport_id = case.membership.id
    with pytest.raises((ProcessError, AssertionError)):
        read(case)


def test_missing_stopped_receipt_never_completes(monkeypatch):
    case = fixture(monkeypatch)
    case.receipts.pop()
    with pytest.raises(ProcessError):
        read(case)
