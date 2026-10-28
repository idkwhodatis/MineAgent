"""Offline oracle regression; native execution evidence is collected separately."""

from datetime import date

import pytest

from packages.factors.contracts import (
    FactorDefinition,
    FeatureRow,
    SnapshotInput,
    rows_digest,
)
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.structured_factor_strategy.assembly import assemble_input
from tests.integration.structured_execution_fixture import assert_engine_oracle
from tests.structured.test_services import material


def engine_case(lookback):
    kwargs = material()
    # Independent arithmetic from the original retained fixture closes, not the
    # result/decision record being checked. No RD runtime receipt is manufactured.
    source = kwargs["source"]
    closes = {(o.listing_id, o.session): o.values[0] for o in source.observations}
    values = {
        listing_id: closes[listing_id, date(2024, 3, 13)]
        / closes[listing_id, date(2024, 3, 13 - lookback)]
        - 1
        for listing_id in source.universe.listing_ids
    }
    rows = tuple(
        FeatureRow.model_validate(
            {**row.model_dump(mode="json"), "value": values[row.listing_id]}
        )
        for row in kwargs["rows"]
    )
    manifest = kwargs["snapshot"].model_dump(mode="json")
    manifest["input"]["definition"]["lookback_sessions"] = lookback
    definition = FactorDefinition.model_validate(manifest["input"]["definition"])
    manifest["input"]["version"]["definition_sha256"] = definition.sha256
    manifest["input_sha256"] = SnapshotInput.model_validate(manifest["input"]).sha256
    manifest["output_rows_sha256"] = rows_digest(rows)
    kwargs.update(
        rows=rows, snapshot=ExecutionSnapshotManifest.model_validate(manifest)
    )
    value = assemble_input(**kwargs)
    return value, kwargs["market"]


def test_frozen_market_contract_requires_prior_warmup_and_two_active_sessions():
    inputs, market = engine_case(2)
    assert inputs.engine.window.start == date(2024, 3, 13)
    assert inputs.engine.window.end == date(2024, 3, 15)
    assert [session["session_date"] for session in market.sessions] == [
        "2024-03-12",
        "2024-03-13",
        "2024-03-14",
    ]
    assert inputs.engine.manifest.data.warmup_bars == 1


def test_independent_source_arithmetic_selects_two_distinct_winners():
    for lookback, winner in ((2, "AAA"), (1, "BBB")):
        inputs, market = engine_case(lookback)
        names = {str(item.listing_id): item.symbol for item in market.listings}
        eligible = sorted(
            (row for row in inputs.rows if row.value > 0),
            key=lambda row: (-row.value, row.listing_id),
        )
        assert names[eligible[0].listing_id] == winner


def _literal_oracle_result():
    from types import SimpleNamespace as NS

    # Hand-authored fixed oracle exercise, not a persisted/runtime receipt.
    warmup = NS(
        event_seq=0,
        observation=NS(session_date=date(2024, 3, 12), warmup=True),
        reply=NS(decision=NS(action="no_decision")),
        reads=(),
        eligible=(),
        ranking=(),
        targets=(),
    )
    signal = NS(
        event_seq=1,
        observation=NS(session_date=date(2024, 3, 13), warmup=False),
        reply=NS(decision=NS(action="rebalance")),
        reads=(1, 2, 3),
        targets=(NS(listing_id="AAA", value=0.5),),
    )
    hold = NS(
        event_seq=2,
        observation=NS(session_date=date(2024, 3, 14), warmup=False),
        reply=NS(decision=NS(action="hold")),
        reads=(),
        targets=(),
    )
    order = {
        "listing_id": "AAA",
        "side": "buy",
        "quantity": 48,
        "filled_quantity": 48,
        "status": "filled",
        "order_id": "order",
        "logical_order_id": "logical",
        "commission": 2.4012,
    }
    fill = {
        "listing_id": "AAA",
        "side": "buy",
        "quantity": 48,
        "order_id": "order",
        "logical_order_id": "logical",
        "session_date": "2024-03-14",
        "decision_session": "2024-03-13",
        "price": 100.05,
        "commission": 2.4012,
    }
    return NS(
        result_trust="closed_operator_trusted_engine",
        decisions=(warmup, signal, hold),
        accounting_tables={
            "orders": [order],
            "fills": [fill],
            "account_series": [{"cash": 5195.1988, "equity": 10475.1988}],
            "summary": {"final_equity": 10475.1988, "execution_count": 1},
        },
    )


def test_strict_oracle_retains_full_accounting_checks():
    assert_engine_oracle(
        _literal_oracle_result(), listing_id="AAA", quantity=48, closing_price=110.0
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_warmup",
        "warmup_trade",
        "warmup_factor_read",
        "wrong_day",
        "wrong_order",
        "wrong_cash",
    ],
)
def test_oracle_rejects_warmup_and_economic_drift(fault):
    result = _literal_oracle_result()
    if fault == "missing_warmup":
        result.decisions = result.decisions[1:]
    elif fault == "warmup_trade":
        result.decisions[0].reply.decision.action = "rebalance"
    elif fault == "warmup_factor_read":
        result.decisions[0].reads = ("unexpected",)
    elif fault == "wrong_day":
        result.decisions[0].observation.session_date = date(2024, 3, 11)
    elif fault == "wrong_order":
        result.accounting_tables["orders"][0]["quantity"] = 49
    else:
        result.accounting_tables["account_series"][-1]["cash"] += 1
    with pytest.raises(AssertionError):
        assert_engine_oracle(result, listing_id="AAA", quantity=48, closing_price=110.0)
