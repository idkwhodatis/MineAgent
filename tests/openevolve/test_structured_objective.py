"""Arithmetic/closed protocol tests, not stopped process or financial evidence."""

from datetime import date

import pytest

from packages.openevolve_structured.objective import (
    StructuredObjective,
    _score,
    project_result,
)


def profile(**kwargs):
    return StructuredObjective(
        initial_cash=10000.0,
        window={"start": date(2024, 3, 13), "end": date(2024, 3, 15)},
        **kwargs,
    )


@pytest.mark.parametrize(
    "returns,drawdown,expected",
    [
        (0.0, 0.0, 0.0),
        (-5.0, 2.0, -7.0),
        (3.0, 1.0, 2.0),
        (3.123456789, 0.123456789, 3.0),
    ],
)
def test_actual_summary_arithmetic_has_no_hidden_trade_penalty(
    returns, drawdown, expected
):
    assert _score(
        {
            "total_return_pct": returns,
            "max_drawdown_pct": drawdown,
            "closed_trade_count": 0,
        },
        profile(),
    ) == (expected, "eligible")


def test_minimum_trades_is_only_explicit_constraint():
    summary = {
        "total_return_pct": 1.0,
        "max_drawdown_pct": 0.0,
        "closed_trade_count": 0,
    }
    assert _score(summary, profile(minimum_closed_trades=1)) == (
        None,
        "minimum_closed_trades",
    )
    assert _score(summary, profile(minimum_closed_trades=0)) == (1.0, "eligible")


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True])
def test_missing_or_nonfinite_is_not_synthetic_zero(value):
    assert _score(
        {"total_return_pct": value, "max_drawdown_pct": 0.0, "closed_trade_count": 0},
        profile(),
    ) == (None, "undefined_accounting")


def test_input_and_complete_result_required_not_detached_score():
    with pytest.raises(ValueError):
        project_result(b"{}", b'{"combined_score":10}', profile())


def test_unchecked_model_copy_bool_not_numeric_policy():
    with pytest.raises(ValueError):
        project_result(
            b"{}", b"{}", profile().model_copy(update={"minimum_closed_trades": True})
        )
