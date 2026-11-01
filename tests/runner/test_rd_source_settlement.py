"""Real settlement accounting with inert source/SQL doubles; not PG proof."""

from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_source_publication as source
from backend.app import research_broker_services as broker
from backend.app import research_workflow_rd_terminal as terminal
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector


@pytest.mark.parametrize(
    "drift", [None, "owner_id", "run_id", "attempt_id", "generation"]
)
def test_source_full_peak_settlement_is_scoped_idempotent_and_closes_membership(
    monkeypatch, drift
):
    fixture = Path("tests/integration/rd_source_fixture.py").read_text()
    assert "settle_source_conservatively" not in fixture
    assert "publish_reserved_source(" in fixture
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence = NS(**scope)
    held = NS(
        **scope,
        id=uuid4(),
        purpose="object_storage",
        state="reserved",
        amounts=BudgetVector(storage_bytes=9000).model_dump(),
    )
    publication = NS(id=uuid4(), budget_reservation_id=held.id, peak_bytes=9000)
    result = NS(publication_id=publication.id)
    budget = NS(reserved=held.amounts.copy(), used=BudgetVector().model_dump())
    settlements = []
    verified = []

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model is ResearchBudgetReservation:
                return held
            assert model is ResearchBudgetSettlement
            return settlements[0] if settlements else None

        def scalars(self, statement):
            model = statement.column_descriptions[0]["entity"]
            return [held] if model is ResearchBudgetReservation else settlements

        def add(self, row):
            settlements.append(row)

        def flush(self):
            pass

    session = Session()

    def finalize(actual_session, actual_fence, *, publication_id, settings):
        assert actual_session is session and actual_fence is fence
        assert publication_id == publication.id
        verified.append(publication_id)
        return result

    monkeypatch.setattr(source, "finalize_source", finalize)
    monkeypatch.setattr(source, "_scope_row", lambda *a: publication)
    monkeypatch.setattr(broker, "_budget", lambda *a: budget)
    with pytest.raises(DomainError, match="Complete owned child"):
        terminal._settlements(session, scope)
    if drift:
        setattr(held, drift, 2 if drift == "generation" else uuid4())
        with pytest.raises(broker.BrokerAbort):
            source._finalize_settled_source(
                session, fence, publication_id=publication.id, settings=None
            )
        assert not settlements and held.state == "reserved"
        assert (
            budget.reserved == held.amounts
            and budget.used == BudgetVector().model_dump()
        )
        return
    for _ in range(2):
        actual, paid = source._finalize_settled_source(
            session, fence, publication_id=publication.id, settings=None
        )
        assert actual is result and paid is settlements[0]
    assert (
        source.settle_source_conservatively(
            session, fence, publication_id=publication.id, settings=None
        )
        is paid
    )
    assert len(verified) == 3  # Replay still freshly verifies the source graph.
    assert len(settlements) == 1 and held.state == "settled"
    assert budget.reserved == BudgetVector().model_dump()
    assert budget.used == held.amounts == paid.charge
    assert paid.outcome == "conservative_unknown"
    assert terminal._settlements(session, scope) == (held.id,)
