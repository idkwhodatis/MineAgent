"""Installation-wide SDK pipe/process holds under the existing research quota lock.

Unknown starts/stops retain their peak. Final full settlement plus immutable stop
membership is required to release transient peak; retained objects count normally.
"""

from sqlalchemy import select, text

from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
    ResearchModelTransport,
)
from packages.research_runtime.budget_contracts import BudgetVector

MAX_CONCURRENT_SDK = 4


def provider_process_holds(session):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
    ):
        return ()
    rows = session.execute(
        select(
            ResearchBudgetReservation,
            ResearchModelTransport,
            ResearchBudgetSettlement,
            RDProviderTransportReceipt,
        )
        .select_from(ResearchBudgetReservation)
        .outerjoin(
            ResearchModelTransport,
            ResearchModelTransport.reservation_id == ResearchBudgetReservation.id,
        )
        .outerjoin(
            ResearchBudgetSettlement,
            ResearchBudgetSettlement.reservation_id == ResearchBudgetReservation.id,
        )
        .outerjoin(
            RDProviderTransportReceipt,
            (RDProviderTransportReceipt.transport_id == ResearchModelTransport.id)
            & (RDProviderTransportReceipt.kind == "stopped"),
        )
        .where(
            ResearchBudgetReservation.purpose == "model_transport",
            ResearchBudgetReservation.amounts["process_slots"].as_integer() > 0,
        )
        .limit(10001)
    ).all()
    if len(rows) > 10000:
        raise ValueError("SDK reservation inventory bound")
    holds = []
    seen = set()
    for reservation, transport, settlement, stop in rows:
        if reservation.id in seen:
            raise ValueError("SDK reservation has multiple physical consumers")
        seen.add(reservation.id)
        amount = BudgetVector.model_validate(reservation.amounts)
        if (
            transport is not None
            and stop is not None
            and settlement is not None
            and reservation.state == "settled"
            and settlement.outcome == "conservative_unknown"
            and settlement.charge == amount.model_dump()
            and transport.state in {"completed", "failed", "unknown"}
            and all(
                getattr(row, k) == getattr(reservation, k)
                for row in (transport, settlement, stop)
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            )
        ):
            continue
        holds.append((reservation.owner_id, amount.storage_bytes, 8))
    return tuple(holds)


def admit_provider_capacity(session, fence, amounts, settings):
    from backend.app.openevolve_process_quota import require_process_capacity
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence, resource_scope="processes")
    if len(provider_process_holds(session)) >= MAX_CONCURRENT_SDK:
        raise ValueError("Installation SDK concurrency bound exhausted")
    require_process_capacity(session, fence, amounts, settings, extra_files=8)


def require_provider_call_capacity(session, fence):
    """Actual first-iteration call cap, independent of a larger user budget.

    Call only inside the serialized fenced admission transaction, after replay
    resolution and before adding the first transport. All attempts in this run
    count; a new lease is not permission to reset physical model-call history.
    """
    from sqlalchemy import func, select

    from backend.app.research_broker_models import ResearchModelOperation
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.services import DomainError
    from packages.rdagent_provider.codec import PROTOCOLS

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    count = session.scalar(
        select(func.count())
        .select_from(ResearchModelOperation)
        .where(
            ResearchModelOperation.owner_id == fence.owner_id,
            ResearchModelOperation.run_id == fence.run_id,
            ResearchModelOperation.protocol.in_(PROTOCOLS),
        )
    )
    if type(count) is not int or not 0 <= count < 32:
        raise DomainError(
            "rd_provider_call_limit",
            "The declared first-iteration model-call bound is exhausted",
            409,
        )
