"""027 declared fixed-window accounting objective, not financial advice or authority.

The host must read a stopped protected execution through its current persisted
resolver before calling this pure projection. Hashes/parsed data cannot grant an
execution or publication. Raw accounting evidence is retained, never replaced
by a population score.
"""

import hashlib
import math
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import DateWindow, digest

from .candidate import Closed


class StructuredObjective(Closed):
    schema_version: Literal["mineagent.oe-structured-objective.v1"] = (
        "mineagent.oe-structured-objective.v1"
    )
    window: DateWindow
    initial_cash: float = Field(strict=True, gt=0, le=1e9)
    metric: Literal["total_return_pct_minus_max_drawdown_pct"] = (
        "total_return_pct_minus_max_drawdown_pct"
    )
    unit: Literal["percentage_points"] = "percentage_points"
    direction: Literal["maximize"] = "maximize"
    source: Literal["validated_closed_engine_accounting_summary"] = (
        "validated_closed_engine_accounting_summary"
    )
    precision: Literal["unrounded_native_accounting"] = "unrounded_native_accounting"
    missing_policy: Literal["ineligible_null_score"] = "ineligible_null_score"
    minimum_closed_trades: int | None = Field(default=None, ge=0, le=1000000)


class StructuredMeasurement(Closed):
    schema_version: Literal["mineagent.oe-structured-measurement.v1"] = (
        "mineagent.oe-structured-measurement.v1"
    )
    execution_id: UUID
    structured_strategy_version_id: UUID
    snapshot_id: UUID
    strategy_input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    market_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    result_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    objective_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    total_return_pct: float | None
    max_drawdown_pct: float | None
    closed_trade_count: int = Field(ge=0)
    eligible: bool
    combined_score: float | None
    reason: Literal["eligible", "undefined_accounting", "minimum_closed_trades"]
    qualification: Literal[
        "fixed_window_comparison_not_causal_advantage_or_annualized_gain"
    ] = "fixed_window_comparison_not_causal_advantage_or_annualized_gain"

    @model_validator(mode="after")
    def exact_score(self):
        if self.eligible != (self.reason == "eligible"):
            raise ValueError("Objective eligibility differs")
        if self.eligible:
            if self.combined_score is None or not math.isfinite(self.combined_score):
                raise ValueError("Eligible objective requires finite score")
        elif self.combined_score is not None:
            raise ValueError("Ineligible objective cannot carry a score")
        return self


def _score(summary, profile):
    trades = summary["closed_trade_count"]
    if type(trades) is not int or trades < 0:
        raise ValueError("Exact native closed-trade count required")
    values = (summary["total_return_pct"], summary["max_drawdown_pct"])
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        return None, "undefined_accounting"
    if values[1] < 0:
        raise ValueError("Nonnegative percentage drawdown required")
    if (
        profile.minimum_closed_trades is not None
        and trades < profile.minimum_closed_trades
    ):
        return None, "minimum_closed_trades"
    value = values[0] - values[1]
    return (
        (float(value), "eligible")
        if math.isfinite(value)
        else (None, "undefined_accounting")
    )


def project_result(strategy_raw, result_raw, profile):
    """Revalidate complete bytes; caller separately owns current stopped authority."""
    if type(profile) is not StructuredObjective or type(result_raw) is not bytes:
        raise ValueError("Exact structured objective and result bytes required")
    # Raw field validation must precede serialization: unchecked bool→int is not a grant.
    from .candidate import _raw_fields

    profile = StructuredObjective.model_validate(_raw_fields(profile))
    from packages.structured_factor_strategy.wire import read_input, read_result

    inputs = read_input(strategy_raw)
    result = read_result(result_raw, inputs)
    if (
        inputs.engine.window != profile.window
        or inputs.engine.initial_cash != profile.initial_cash
    ):
        raise ValueError("Accounting objective window differs from actual execution")
    summary = result.accounting_tables["summary"]
    score, reason = _score(summary, profile)
    return StructuredMeasurement(
        execution_id=result.execution_id,
        structured_strategy_version_id=result.structured_strategy_version_id,
        snapshot_id=result.snapshot_id,
        strategy_input_sha256=result.strategy_input_sha256,
        market_sha256=result.market_sha256,
        result_sha256=hashlib.sha256(result_raw).hexdigest(),
        objective_sha256=digest(profile),
        total_return_pct=summary["total_return_pct"],
        max_drawdown_pct=summary["max_drawdown_pct"],
        closed_trade_count=summary["closed_trade_count"],
        eligible=reason == "eligible",
        combined_score=score,
        reason=reason,
    )
