"""Mutation accounting dispatch does not grant caller-config authority."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app import oe_structured_identity
from backend.app import research_broker_services as ledger
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.control import BrokerAbort
from packages.research_runtime.fences import AttemptFence


@pytest.mark.parametrize(
    "revision,valid",
    [
        ("028_oe_structured_search", True),
        ("026_rd_seeded_submissions", False),
        ("028_unknown", False),
    ],
)
def test_exact_schema_and_persisted_spec_before_mutation_hold(
    monkeypatch, revision, valid
):
    session = MagicMock()
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    run = NS(
        purpose="strategy_search",
        engine="openevolve",
        config={"schema_version": "mineagent.oe-structured-provider-run.v1"},
    )
    session.get.return_value = run
    session.scalars.return_value.all.return_value = [revision]
    existing = NS(amounts=BudgetVector(mutation_slots=1).model_dump())
    session.scalar.return_value = existing
    monkeypatch.setattr(ledger, "_budget", lambda *a: object())
    validate = MagicMock()
    monkeypatch.setattr(oe_structured_identity, "decode_spec", validate)

    def invoke():
        return ledger.reserve_budget(
            session,
            fence,
            purpose="engine_mutation",
            logical_key="oe:1",
            amounts=BudgetVector(mutation_slots=1),
        )

    if valid:
        assert invoke() is existing
        validate.assert_called_once_with(session, run)
    else:
        with pytest.raises(BrokerAbort):
            invoke()
        validate.assert_not_called()
        session.scalar.assert_not_called()
    session.add.assert_not_called()
