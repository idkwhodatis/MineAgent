"""Released result DTO projections, never storage, execution or disclosure grants."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.factors.contracts import FactorDefinition
from packages.structured_factor_strategy.operator import DecisionRecord
from packages.structured_factor_strategy.wire import StructuredResult


class View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ObservedOrder(View):
    native_order_id: str = Field(min_length=1, max_length=200)
    logical_order_id: str = Field(pattern="^[a-f0-9]{64}$")
    decision_sequence: int = Field(ge=0, lt=8)
    listing_id: UUID
    side: Literal["buy", "sell"]
    quantity: float
    filled_quantity: float
    status: str = Field(max_length=100)
    commission: float


class ObservedFill(View):
    native_fill_id: str = Field(min_length=1, max_length=200)
    native_order_id: str = Field(min_length=1, max_length=200)
    logical_order_id: str = Field(pattern="^[a-f0-9]{64}$")
    listing_id: UUID
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    commission: float
    session_date: date


class StructuredExecutionView(View):
    schema_version: Literal["mineagent.structured-execution-view.v1"] = (
        "mineagent.structured-execution-view.v1"
    )
    execution_id: UUID
    run_id: UUID
    structured_strategy_version_id: UUID
    factor_version_id: UUID
    factor_id: UUID
    parent_factor_version_id: UUID | None
    execution_snapshot_id: UUID
    execution_snapshot_run_id: UUID
    factor_definition: FactorDefinition
    factor_code_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    snapshot_input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    result_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    market_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    program_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    decisions: tuple[DecisionRecord, ...] = Field(min_length=1, max_length=8)
    orders: tuple[ObservedOrder, ...] = Field(max_length=1000)
    fills: tuple[ObservedFill, ...] = Field(max_length=10000)
    qualification: Literal[
        "closed_operator_trusted_engine_not_arbitrary_strategy_causality"
    ] = "closed_operator_trusted_engine_not_arbitrary_strategy_causality"


def execution_view(history):
    """Caller must separately verify and release the exact historical buffer."""
    result = StructuredResult.model_validate(history.result)
    by_logical = {
        key: item["sequence"]
        for item in result.accounting_tables["decision_trace"]
        for key in item["logical_order_ids"]
    }
    orders = tuple(
        ObservedOrder(
            native_order_id=row["order_id"],
            decision_sequence=by_logical[row["logical_order_id"]],
            **{
                k: row[k]
                for k in (
                    "logical_order_id",
                    "listing_id",
                    "side",
                    "quantity",
                    "filled_quantity",
                    "status",
                    "commission",
                )
            },
        )
        for row in result.accounting_tables["orders"]
    )
    fills = tuple(
        ObservedFill(
            native_fill_id=row["fill_id"],
            native_order_id=row["order_id"],
            **{
                k: row[k]
                for k in (
                    "logical_order_id",
                    "listing_id",
                    "side",
                    "quantity",
                    "price",
                    "commission",
                    "session_date",
                )
            },
        )
        for row in result.accounting_tables["fills"]
    )
    return StructuredExecutionView(
        execution_id=history.execution_id,
        run_id=history.run_id,
        structured_strategy_version_id=history.structured_strategy_version_id,
        factor_version_id=history.factor_version_id,
        factor_id=history.factor_id,
        parent_factor_version_id=history.parent_factor_version_id,
        execution_snapshot_id=history.snapshot_id,
        execution_snapshot_run_id=history.execution_snapshot_run_id,
        factor_definition=history.factor_definition,
        factor_code_sha256=history.factor_code_sha256,
        snapshot_input_sha256=history.snapshot_input_sha256,
        result_sha256=history.result_sha256,
        market_sha256=result.market_sha256,
        program_sha256=result.decisions[0].program_sha256,
        decisions=result.decisions,
        orders=orders,
        fills=fills,
    )


class ListingValueDifference(View):
    listing_id: UUID
    before_value: float | None
    after_value: float | None
    before_availability: str
    after_availability: str
    observed_change: bool


class StructuredPairComparison(View):
    schema_version: Literal["mineagent.structured-pair-comparison.v1"] = (
        "mineagent.structured-pair-comparison.v1"
    )
    before: StructuredExecutionView
    after: StructuredExecutionView
    values: tuple[ListingValueDifference, ...] = Field(max_length=100)
    eligibility_changed: bool
    score_values_changed: bool
    ranking_changed: bool
    targets_changed: bool
    observed_orders_changed: bool
    observed_fills_changed: bool
    qualification: Literal[
        "same_market_and_closed_program_observed_pair_not_financial_improvement"
    ] = "same_market_and_closed_program_observed_pair_not_financial_improvement"


def compare_released_pair(before_history, after_history):
    """Exact pair projection; no inference that a target was submitted or filled."""
    before, after = execution_view(before_history), execution_view(after_history)
    left, right = before_history.result, after_history.result
    if (
        before.execution_id == after.execution_id
        or before.factor_version_id == after.factor_version_id
        or (
            before.run_id,
            before.structured_strategy_version_id,
            before.market_sha256,
            before.program_sha256,
        )
        != (
            after.run_id,
            after.structured_strategy_version_id,
            after.market_sha256,
            after.program_sha256,
        )
    ):
        raise ValueError(
            "Same declared market/program and distinct factor versions required"
        )
    for key in ("adapter_manifest", "window", "engine"):
        if left.accounting_tables[key] != right.accounting_tables[key]:
            raise ValueError("Pair engine/window/initial-accounting profile differs")
    a = [x for x in before.decisions if x.reads]
    b = [x for x in after.decisions if x.reads]
    if (
        len(a) != 1
        or len(b) != 1
        or a[0].observation.session_date != b[0].observation.session_date
    ):
        raise ValueError("Exact shared single signal session required")
    ar = {x.native_listing_id: x for x in a[0].reads}
    br = {x.native_listing_id: x for x in b[0].reads}
    if ar.keys() != br.keys():
        raise ValueError("Pair listing inventories differ")
    values = tuple(
        ListingValueDifference(
            listing_id=key,
            before_value=ar[key].value,
            after_value=br[key].value,
            before_availability=ar[key].availability,
            after_availability=br[key].availability,
            observed_change=(ar[key].value, ar[key].availability)
            != (br[key].value, br[key].availability),
        )
        for key in sorted(ar, key=str)
    )

    # Native IDs necessarily differ between units, so compare observed economics,
    # not process-local identifiers; retain identifiers in each full linked view.
    def order_values(view):
        return sorted(
            (
                str(x.listing_id),
                x.side,
                x.quantity,
                x.filled_quantity,
                x.status,
                x.commission,
            )
            for x in view.orders
        )

    def fill_values(view):
        return sorted(
            (
                str(x.listing_id),
                x.side,
                x.quantity,
                x.price,
                x.commission,
                x.session_date,
            )
            for x in view.fills
        )

    return StructuredPairComparison(
        before=before,
        after=after,
        values=values,
        eligibility_changed=a[0].eligible != b[0].eligible,
        score_values_changed=a[0].ranking != b[0].ranking,
        ranking_changed=tuple(x.listing_id for x in a[0].ranking)
        != tuple(x.listing_id for x in b[0].ranking),
        targets_changed=a[0].targets != b[0].targets,
        observed_orders_changed=order_values(before) != order_values(after),
        observed_fills_changed=fill_values(before) != fill_values(after),
    )
