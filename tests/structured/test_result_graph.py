"""Projection/transaction unit doubles, not actual process or fill evidence."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.services import DomainError
from backend.app.structured_factor_results import result_graph
from packages.structured_factor_strategy.operator import evaluate
from tests.structured.test_operator import fixture


def case():
    inputs, observation, _ = fixture()
    decision = evaluate(inputs, observation, 0)
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    result = NS(
        execution_id=inputs.execution_id,
        decisions=(decision,),
        accounting_tables={
            "decision_trace": [{"sequence": 0, "logical_order_ids": []}],
            "orders": [],
            "fills": [],
        },
    )
    return fence, result


def test_projection_never_invents_order_or_fill_from_target():
    fence, result = case()
    decisions, orders, fills = result_graph(fence, result)
    assert decisions[0]["record"]["targets"]
    assert orders == fills == []
    assert decisions[0]["record"]["reads"][0]["value"] == 0.04


def test_observed_native_order_and_fill_remain_distinct_linked_rows():
    fence, result = case()
    logical = "a" * 64
    listing = str(uuid4())
    result.accounting_tables["decision_trace"][0]["logical_order_ids"] = [logical]
    result.accounting_tables["orders"] = [
        {
            "order_id": "actual-order",
            "logical_order_id": logical,
            "listing_id": listing,
            "quantity": 48.0,
            "status": "filled",
        }
    ]
    result.accounting_tables["fills"] = [
        {
            "fill_id": "actual-fill",
            "order_id": "actual-order",
            "quantity": 48.0,
            "price": 100.05,
        }
    ]
    _, orders, fills = result_graph(fence, result)
    assert orders[0]["event_seq"] == 0 and orders[0]["logical_order_id"] == logical
    assert fills[0]["native_order_id"] == orders[0]["native_order_id"]
    assert fills[0]["record"]["price"] == 100.05


def test_unattributed_native_order_is_not_assigned_by_guess():
    fence, result = case()
    result.accounting_tables["orders"] = [
        {"order_id": "order", "logical_order_id": "a" * 64, "listing_id": str(uuid4())}
    ]
    with pytest.raises(DomainError):
        result_graph(fence, result)


def test_duplicate_logical_order_across_decisions_rejects():
    fence, result = case()
    result.accounting_tables["decision_trace"] = [
        {"sequence": i, "logical_order_ids": ["a" * 64]} for i in (0, 1)
    ]
    with pytest.raises(DomainError):
        result_graph(fence, result)
