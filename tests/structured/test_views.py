"""Released DTO projection doubles, never native process/accounting acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from packages.factors.contracts import digest
from packages.structured_factor_strategy.operator import evaluate
from packages.structured_factor_strategy.views import compare_released_pair
from packages.structured_factor_strategy.wire import StructuredResult
from tests.structured.test_operator import declared_input, fixture


def histories(*, changed):
    run_id, version_id = uuid4(), uuid4()
    results = []
    for i, values in enumerate(
        (
            (0.04, 0.02, -0.02),
            (1 / 103, 1 / 101, -1 / 99) if changed else (0.04, 0.02, -0.02),
        )
    ):
        inputs, observation, _ = fixture(values)
        decision = evaluate(inputs, observation, 0)
        listing = decision.targets[0].listing_id
        quantity = 49.0 if i and changed else 48.0
        logical = "a" * 64
        native = f"order-{i}"
        tables = {
            "adapter_manifest": {"fixed": "projection-double"},
            "window": {"fixed": "same"},
            "engine": {"fixed": "same"},
            "decision_trace": [{"sequence": 0, "logical_order_ids": [logical]}],
            "orders": [
                {
                    "order_id": native,
                    "logical_order_id": logical,
                    "listing_id": listing,
                    "side": "buy",
                    "quantity": quantity,
                    "filled_quantity": quantity,
                    "status": "filled",
                    "commission": quantity * 100.05 * 0.0005,
                }
            ],
            "fills": [
                {
                    "fill_id": f"fill-{i}",
                    "order_id": native,
                    "logical_order_id": logical,
                    "listing_id": listing,
                    "side": "buy",
                    "quantity": quantity,
                    "price": 100.05,
                    "commission": quantity * 100.05 * 0.0005,
                    "session_date": "2024-03-14",
                }
            ],
        }
        declared = declared_input()
        result = StructuredResult(
            execution_id=inputs.execution_id,
            structured_strategy_version_id=version_id,
            snapshot_id=inputs.snapshot.snapshot_id,
            strategy_input_sha256="b" * 64,
            market_sha256="c" * 64,
            decisions=(decision,),
            accounting_tables=tables,
        )
        results.append(
            NS(
                execution_id=inputs.execution_id,
                run_id=run_id,
                structured_strategy_version_id=version_id,
                factor_version_id=inputs.snapshot.input.version.factor_version_id,
                factor_id=uuid4(),
                parent_factor_version_id=None,
                factor_definition=declared.snapshot.input.definition,
                factor_code_sha256=declared.snapshot.input.version.code_sha256,
                snapshot_input_sha256=declared.snapshot.input_sha256,
                snapshot_id=inputs.snapshot.snapshot_id,
                execution_snapshot_run_id=uuid4(),
                result_sha256=digest(result),
                result=result,
            )
        )
    return results


def test_changed_values_ranking_targets_and_observed_fills_are_separate():
    comparison = compare_released_pair(*histories(changed=True))
    assert comparison.before.execution_snapshot_run_id != comparison.before.run_id
    assert all(x.observed_change for x in comparison.values)
    assert comparison.score_values_changed and comparison.ranking_changed
    assert not comparison.eligibility_changed
    assert (
        comparison.targets_changed
        and comparison.observed_orders_changed
        and comparison.observed_fills_changed
    )
    assert (
        comparison.before.orders[0].native_order_id
        == comparison.before.fills[0].native_order_id
    )


def test_distinct_native_ids_do_not_falsely_report_economic_change():
    comparison = compare_released_pair(*histories(changed=False))
    assert not any(x.observed_change for x in comparison.values)
    assert not comparison.ranking_changed and not comparison.targets_changed
    assert (
        not comparison.observed_orders_changed and not comparison.observed_fills_changed
    )


def test_other_run_not_a_controlled_pair():
    first, second = histories(changed=True)
    second.run_id = uuid4()
    with pytest.raises(ValueError, match="Same declared"):
        compare_released_pair(first, second)


def test_version_parameters_and_code_are_actual_detached_history_not_inferred():
    first, second = histories(changed=True)
    comparison = compare_released_pair(first, second)
    assert comparison.before.factor_definition == first.factor_definition
    assert comparison.after.factor_definition == second.factor_definition
    assert comparison.before.factor_code_sha256 == first.factor_code_sha256
    assert comparison.after.snapshot_input_sha256 == second.snapshot_input_sha256


def test_pair_never_infers_parent_from_changed_parameter_or_order():
    first, second = histories(changed=True)
    comparison = compare_released_pair(first, second)
    assert comparison.before.factor_id == first.factor_id
    assert comparison.after.factor_id == second.factor_id
    assert comparison.before.parent_factor_version_id is None
    assert comparison.after.parent_factor_version_id is None
