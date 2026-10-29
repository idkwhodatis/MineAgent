"""Flush-only admission order with real ORM constructors and request DTOs."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_admission as service
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_accounting import request_reservation
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_contracts import declared


def setup(monkeypatch):
    from packages.openevolve_structured.provider_contracts import ChatPricing
    from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
    from packages.openevolve_structured.provider_response import build_request

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
    scope = {
        "owner_id": context.owner_id,
        "run_id": context.run_id,
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence = NS(**scope)
    peer = object()
    bound = NS(request=request, context=context, config=NS(model_binding=binding))
    added, events = [], []
    state = NS(previous=None, held="reserved", aborted=False, operation=False)

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model is service.OEStructuredProviderTransport:
                return state.previous
            if model is service.ResearchContextAbort:
                return NS() if state.aborted else None
            return NS() if state.operation else None

        def scalars(self, statement):
            return NS(all=list)

        def add(self, row):
            added.append(row)

        def flush(self):
            events.append("flush")

    def require(*a, **kw):
        assert kw["peer"] is peer
        events.append("require")
        return bound

    def reserve(*a, **kw):
        assert kw["amounts"] == request_reservation(request, binding)
        assert kw["purpose"] == "model_transport"
        assert kw["logical_key"] == "oe-structured-provider:" + str(
            context.iteration_intent_id
        )
        events.append("reserve")
        return NS(id=uuid4(), state=state.held)

    from backend.app import rdagent_provider_quota

    monkeypatch.setattr(
        rdagent_provider_quota,
        "admit_provider_capacity",
        lambda *a, **kw: events.append("quota"),
    )
    monkeypatch.setattr(service, "require_provider_operation", require)
    monkeypatch.setattr(service, "reserve_budget", reserve)
    monkeypatch.setattr(
        service, "read_research_object", lambda *a: canonical_bytes(request)
    )
    session = Session()

    def call():
        return service.admit_provider_transport(
            session,
            fence,
            context.iteration_intent_id,
            uuid4(),
            canonical_bytes(request),
            NS(),
            peer=peer,
        )

    return NS(call=call, state=state, events=events, added=added)


def test_exact_one_submitted_sdk_membership_no_implicit_commit(monkeypatch):
    case = setup(monkeypatch)
    member, operation, transport = case.call()
    assert member.operation_id == operation.id == transport.operation_id
    assert member.transport_id == transport.id
    assert operation.state == "open" and transport.state == "submitted"
    assert transport.ordinal == 1 and operation.ordinal == 0
    assert case.events.index("quota") < case.events.index("reserve")
    assert case.events[-1] == "require"
    assert len(case.added) == 4  # context + operation + transport + membership


def test_existing_partial_membership_never_authorizes_retry(monkeypatch):
    case = setup(monkeypatch)
    case.state.previous = NS(id=uuid4())
    with pytest.raises(ValueError, match="already physically admitted"):
        case.call()
    assert "reserve" not in case.events and not case.added


@pytest.mark.parametrize(
    "field,value", [("held", "settled"), ("aborted", True), ("operation", True)]
)
def test_closed_or_existing_context_never_creates_transport(monkeypatch, field, value):
    case = setup(monkeypatch)
    setattr(case.state, field, value)
    with pytest.raises(ProcessError):
        case.call()
    assert not any(
        isinstance(row, service.ResearchModelTransport) for row in case.added
    )
