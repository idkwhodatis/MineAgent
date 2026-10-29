"""One atomic rendered-operation/physical-SDK admission, no implicit retry."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_operational_models import OEStructuredProviderTransport
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.research_broker_models import (
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchModelTransport,
    ResearchOperationContext,
)
from backend.app.research_broker_services import reserve_budget
from backend.app.research_object_services import read_research_object
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_accounting import request_reservation

REQUEST_KIND = "oe_structured_provider_request"
PROTOCOL = "mineagent.oe-structured-provider-chat.v1"


def admit_provider_transport(
    session, fence, intent_id, request_object_id, request_raw, settings, *, peer
):
    """Flush-only. Commit this submitted row BEFORE SDK process creation.

    Existing membership is immutable replay metadata, NEVER permission to repeat
    physical submission. The caller must use completed-response read instead.
    """
    bound = require_provider_operation(session, fence, intent_id, settings, peer=peer)
    request = bound.request
    if type(request_raw) is not bytes or canonical_bytes(request) != request_raw:
        fail()
    if (
        read_research_object(
            session, fence, request_object_id, REQUEST_KIND, request.sha256, settings
        )
        != request_raw
    ):
        fail()
    previous = session.scalar(
        select(OEStructuredProviderTransport).filter_by(iteration_intent_id=intent_id)
    )
    if previous is not None:
        # Never return a transport that the caller could mistake for new admission.
        raise ValueError("Structured provider operation already physically admitted")
    amount = request_reservation(request, bound.config.model_binding)
    from backend.app.rdagent_provider_quota import admit_provider_capacity

    # The027 installation combines RD+OE SDK holds; until installed this path
    # remains rejected by current versioned fence/capability/profile guards.
    admit_provider_capacity(session, fence, amount, settings)
    reservation = reserve_budget(
        session,
        fence,
        purpose="model_transport",
        logical_key="oe-structured-provider:" + str(intent_id),
        amounts=amount,
        settings=settings,
    )
    if reservation.state != "reserved":
        fail()
    scope = {
        k: getattr(fence, k) for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    contexts = session.scalars(
        select(ResearchOperationContext).filter_by(
            **scope, context_sha256=request.context_sha256
        )
    ).all()
    if len(contexts) > 1:
        fail()
    if contexts:
        context = scoped(contexts[0], fence)
        if context.context != bound.context.model_dump(mode="json"):
            fail()
    else:
        context = ResearchOperationContext(
            id=uuid4(),
            **scope,
            context=bound.context.model_dump(mode="json"),
            context_sha256=request.context_sha256,
        )
        session.add(context)
        session.flush()
    if (
        session.scalar(select(ResearchContextAbort).filter_by(context_id=context.id))
        is not None
    ):
        fail()
    if (
        session.scalar(
            select(ResearchModelOperation).filter_by(
                context_id=context.id, purpose="chat", ordinal=0
            )
        )
        is not None
    ):
        fail()
    operation = ResearchModelOperation(
        id=uuid4(),
        **scope,
        protocol=PROTOCOL,
        context_id=context.id,
        purpose="chat",
        ordinal=0,
        request=request.model_dump(mode="json"),
        request_object_id=request_object_id,
        request_object_kind=REQUEST_KIND,
        request_sha256=request.sha256,
        state="open",
    )
    transport = ResearchModelTransport(
        id=uuid4(),
        **scope,
        operation_id=operation.id,
        ordinal=1,
        reservation_id=reservation.id,
        state="submitted",
    )
    member = OEStructuredProviderTransport(
        id=uuid4(),
        **scope,
        iteration_intent_id=intent_id,
        operation_id=operation.id,
        transport_id=transport.id,
        request_sha256=request.sha256,
        model_binding_sha256=bound.config.model_binding.sha256,
    )
    session.add(operation)
    session.add(transport)
    session.add(member)
    session.flush()
    if (
        require_provider_operation(session, fence, intent_id, settings, peer=peer)
        != bound
    ):
        fail()
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    if type(bound.config) is WorkflowStructuredRunConfig:
        from backend.app.oe_workflow_prompt_guard import bind_workflow_consumption

        bind_workflow_consumption(
            session, fence, bound.config, member, operation, transport, request
        )
    return member, operation, transport
