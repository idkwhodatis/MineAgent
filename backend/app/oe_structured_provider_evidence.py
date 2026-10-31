"""Fresh retained027 response/physical/accounting evidence; never DTO authority."""

from sqlalchemy import select, text

from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_operational_models import (
    OEStructuredProviderReceipt,
    OEStructuredProviderTransport,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
    ResearchOperationContext,
)
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_services import read_research_object
from packages.openevolve_structured.provider_accounting import validate_completed_usage
from packages.openevolve_structured.provider_contracts import (
    OEChatRequest,
    OEChatResponse,
)
from packages.openevolve_structured.provider_receipts import (
    OBJECT_KIND,
    parse_receipt,
    validate_chain,
)
from packages.openevolve_structured.provider_response import response_bytes
from packages.research_runtime.budget_contracts import BudgetVector


def read_completed_response(session, fence, exchange, config, settings):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        fail()
    scoped(exchange, fence)
    membership = scoped(
        session.get(OEStructuredProviderTransport, exchange.provider_transport_id),
        fence,
    )
    transport = scoped(
        session.get(ResearchModelTransport, membership.transport_id), fence
    )
    operation = scoped(
        session.get(ResearchModelOperation, membership.operation_id), fence
    )
    if (
        membership.iteration_intent_id != exchange.iteration_intent_id
        or transport.operation_id != operation.id
        or transport.ordinal != 1
        or transport.state != "completed"
        or operation.state != "completed"
        or operation.protocol != "mineagent.oe-structured-provider-chat.v1"
        or operation.purpose != "chat"
        or operation.ordinal != 0
        or operation.request_object_id != exchange.request_object_id
        or operation.request_object_kind != exchange.request_kind
        or operation.request_sha256 != exchange.request_sha256
        or membership.request_sha256 != exchange.request_sha256
        or membership.model_binding_sha256 != config.model_binding.sha256
    ):
        fail()
    physical_attempts = session.scalars(
        select(ResearchModelTransport).filter_by(operation_id=operation.id)
    ).all()
    if len(physical_attempts) != 1 or physical_attempts[0].id != transport.id:
        fail()
    context = scoped(session.get(ResearchOperationContext, operation.context_id), fence)
    if (
        context.context != exchange.context
        or context.context_sha256 != exchange.context_sha256
    ):
        fail()
    if (
        session.scalar(select(ResearchContextAbort).filter_by(context_id=context.id))
        is not None
    ):
        fail()
    request_raw = read_research_object(
        session,
        fence,
        exchange.request_object_id,
        exchange.request_kind,
        exchange.request_sha256,
        settings,
    )
    request = OEChatRequest.model_validate_json(request_raw)
    if (
        request.sha256 != exchange.request_sha256
        or request.model_dump(mode="json") != operation.request
    ):
        fail()
    outcomes = session.scalars(
        select(ResearchModelOutcome).filter_by(transport_id=transport.id)
    ).all()
    if len(outcomes) != 1:
        fail()
    outcome = scoped(outcomes[0], fence)
    if (
        outcome.outcome,
        outcome.response_object_id,
        outcome.response_object_kind,
        outcome.response_sha256,
    ) != (
        "completed",
        exchange.response_object_id,
        exchange.response_kind,
        exchange.response_sha256,
    ):
        fail()
    response_raw = read_research_object(
        session,
        fence,
        exchange.response_object_id,
        exchange.response_kind,
        exchange.response_sha256,
        settings,
    )
    response = OEChatResponse.model_validate_json(response_raw)
    if response_bytes(response) != response_raw:
        fail()
    response, held = validate_completed_usage(request, response, config.model_binding)
    reservation = scoped(
        session.get(ResearchBudgetReservation, transport.reservation_id), fence
    )
    settlements = session.scalars(
        select(ResearchBudgetSettlement).filter_by(reservation_id=reservation.id)
    ).all()
    if len(settlements) != 1:
        fail()
    settlement = scoped(settlements[0], fence)
    if (
        reservation.state != "settled"
        or reservation.purpose != "model_transport"
        or reservation.logical_key
        != "oe-structured-provider:" + str(exchange.iteration_intent_id)
        or BudgetVector.model_validate(reservation.amounts) != held
        or settlement.outcome != "conservative_unknown"
        or BudgetVector.model_validate(settlement.charge) != held
        or BudgetVector.model_validate(outcome.usage) != held
    ):
        fail()
    read_physical_chain(
        session,
        fence,
        membership,
        request,
        config.model_binding,
        settings,
        completed=True,
    )
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    return request, response


def read_physical_chain(
    session, fence, membership, request, binding, settings, *, completed
):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        fail()
    scoped(membership, fence)
    rows = session.scalars(
        select(OEStructuredProviderReceipt).filter_by(membership_id=membership.id)
    ).all()
    if len(rows) != 3 or {row.kind for row in rows} != {"intent", "started", "stopped"}:
        fail()
    documents = {}
    for row in rows:
        scoped(row, fence)
        if row.receipt_object_kind != OBJECT_KIND:
            fail()
        raw = read_research_object(
            session,
            fence,
            row.receipt_object_id,
            OBJECT_KIND,
            row.receipt_sha256,
            settings,
        )
        document = parse_receipt(raw)
        if (
            len(raw) != row.size_bytes
            or document.kind != row.kind
            or document.transport_id != membership.transport_id
            or document.request_sha256 != request.sha256
            or document.model_binding_sha256 != binding.sha256
        ):
            fail()
        scoped(document, fence)
        documents[row.kind] = document
    if documents["intent"].maximum_duration_ms != binding.request_deadline_ms:
        fail()
    validate_chain(
        documents["intent"],
        documents["started"],
        documents["stopped"],
        completed=completed,
    )
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    return documents
