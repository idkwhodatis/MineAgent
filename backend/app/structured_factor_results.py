"""Atomic read/stage/native-order graph publication after actual whole-unit stop.

The protected result file remains charged to its process reservation. These rows
index that exact bounded result; they never manufacture fills from target math.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.structured_factor_models import (
    StructuredDecision,
    StructuredExecutionOutcome,
    StructuredFill,
    StructuredOrder,
)
from backend.app.structured_factor_services import (
    SCOPE,
    fail,
    require_structured_execution,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.structured_factor_strategy.wire import StructuredResult


@dataclass(frozen=True)
class VerifiedStructuredExecution:
    execution_id: UUID
    snapshot_id: UUID
    admission_id: UUID
    result_sha256: str
    result: StructuredResult
    qualification: str = (
        "actual_closed_transform_and_native_accounting_not_financial_gain"
    )


def _record(value):
    raw = canonical_bytes(value)
    return {
        "record": value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else value,
        "record_canonical": raw.decode("ascii"),
        "record_sha256": digest(value),
    }


def result_graph(fence, result):
    """Projection only; publisher must freshly resolve stopped physical result."""
    scope = {k: getattr(fence, k) for k in SCOPE}
    decisions = []
    for item in result.decisions:
        decisions.append(
            {
                "execution_id": result.execution_id,
                **scope,
                "event_seq": item.event_seq,
                **{
                    k: getattr(item, k)
                    for k in (
                        "observation_sha256",
                        "eligibility_sha256",
                        "score_sha256",
                        "allocation_sha256",
                        "reply_sha256",
                    )
                },
                "signal_id": item.reply.decision.signal_id,
                **_record(item),
            }
        )
    sequence = {}
    for trace in result.accounting_tables["decision_trace"]:
        for key in trace["logical_order_ids"]:
            if key in sequence:
                fail()
            sequence[key] = trace["sequence"]
    orders = []
    for item in result.accounting_tables["orders"]:
        if (
            item["logical_order_id"] not in sequence
            or type(item["order_id"]) is not str
            or not 0 < len(item["order_id"]) <= 200
        ):
            fail()
        orders.append(
            {
                "execution_id": result.execution_id,
                "owner_id": fence.owner_id,
                "event_seq": sequence[item["logical_order_id"]],
                "native_order_id": item["order_id"],
                "logical_order_id": item["logical_order_id"],
                "listing_id": UUID(item["listing_id"]),
                **_record(item),
            }
        )
    fills = []
    for item in result.accounting_tables["fills"]:
        if type(item["fill_id"]) is not str or not 0 < len(item["fill_id"]) <= 200:
            fail()
        fills.append(
            {
                "execution_id": result.execution_id,
                "owner_id": fence.owner_id,
                "native_fill_id": item["fill_id"],
                "native_order_id": item["order_id"],
                **_record(item),
            }
        )
    if (
        len(orders) > 1000
        or len(fills) > 10000
        or len({x["native_order_id"] for x in orders}) != len(orders)
        or len({x["native_fill_id"] for x in fills}) != len(fills)
    ):
        fail()
    return decisions, orders, fills


def _expected(receipt, result):
    return {
        "admission_id": receipt.admission.id,
        "launch_identity_receipt_id": receipt.launch_identity_receipt_id,
        "launch_identity_kind": "launch_identity",
        "launch_identity_sha256": receipt.launch_identity_receipt_hash,
        "stop_receipt_id": receipt.stop_receipt_id,
        "stop_kind": "stop",
        "stop_sha256": receipt.stop_receipt_hash,
        "extraction_receipt_id": receipt.extraction_receipt_id,
        "extraction_kind": "extraction",
        "extraction_sha256": receipt.extraction_receipt_hash,
        "result": result.model_dump(mode="json"),
        "result_canonical": receipt.result_bytes.decode("ascii"),
        "result_sha256": receipt.sha256,
        "result_size_bytes": receipt.size_bytes,
        "decision_count": len(result.decisions),
    }


def _reservation(session, fence, receipt, *, completed):
    row = session.get(ResearchBudgetReservation, receipt.admission.reservation_id)
    if (
        row is None
        or any(getattr(row, k) != getattr(fence, k) for k in SCOPE)
        or row.purpose != "process_unit"
        or row.logical_key != "structured-factor-execution:" + str(receipt.execution_id)
    ):
        fail()
    amount = BudgetVector.model_validate(row.amounts)
    if not amount.covers(
        BudgetVector(
            process_slots=1,
            evaluator_executions=1,
            wall_milliseconds=90000,
            storage_bytes=268500992,
        )
    ):
        fail()
    settled = session.scalar(
        select(ResearchBudgetSettlement).filter_by(reservation_id=row.id)
    )
    if row.state == "settled":
        if (
            settled is None
            or any(getattr(settled, k) != getattr(fence, k) for k in SCOPE)
            or settled.charge != row.amounts
            or settled.outcome != "conservative_unknown"
        ):
            fail()
    elif completed or row.state not in {"reserved", "uncertain"} or settled is not None:
        fail()
    return row, amount


def _read(session, fence, execution_id, admission_id, settings):
    from backend.app.structured_process_services import read_stopped_structured

    binding = require_structured_execution(session, fence, execution_id)
    receipt, result = read_stopped_structured(session, fence, admission_id, settings)
    if (
        receipt.execution_id != execution_id
        or result.execution_id != execution_id
        or result.snapshot_id != binding.snapshot_id
        or receipt.result_bytes != canonical_bytes(result)
        or digest(result) != receipt.sha256
    ):
        fail()
    return binding, receipt, result


def _check_rows(session, fence, result):
    expected = result_graph(fence, result)
    for model, values, key in zip(
        (StructuredDecision, StructuredOrder, StructuredFill),
        expected,
        ("event_seq", "native_order_id", "native_fill_id"),
        strict=True,
    ):
        rows = list(
            session.scalars(
                select(model)
                .filter_by(execution_id=result.execution_id)
                .order_by(getattr(model, key))
                .limit(len(values) + 1)
            )
        )
        values = sorted(values, key=lambda x: x[key])
        if len(rows) != len(values) or any(
            any(getattr(row, k) != v for k, v in value.items())
            for row, value in zip(rows, values, strict=True)
        ):
            fail()


def read_verified_structured_execution(session, fence, execution_id, settings):
    binding = require_structured_execution(session, fence, execution_id)
    outcome = session.get(StructuredExecutionOutcome, execution_id)
    if outcome is None or any(getattr(outcome, k) != getattr(fence, k) for k in SCOPE):
        fail()
    _, receipt, result = _read(
        session, fence, execution_id, outcome.admission_id, settings
    )
    if any(getattr(outcome, k) != v for k, v in _expected(receipt, result).items()):
        fail()
    _reservation(session, fence, receipt, completed=True)
    _check_rows(session, fence, result)
    return VerifiedStructuredExecution(
        execution_id, binding.snapshot_id, receipt.admission.id, receipt.sha256, result
    )


def persist_structured_execution_outcome(
    session, fence, execution_id, admission_id, settings
):
    """Flush-only graph + full conservative settlement, all-or-nothing transaction."""
    from backend.app.research_broker_services import settle_budget

    binding, receipt, result = _read(
        session, fence, execution_id, admission_id, settings
    )
    previous = session.get(StructuredExecutionOutcome, execution_id)
    if previous is not None:
        if previous.admission_id != admission_id:
            fail()
        return read_verified_structured_execution(
            session, fence, execution_id, settings
        )
    reservation, amount = _reservation(session, fence, receipt, completed=False)
    session.add(
        StructuredExecutionOutcome(
            execution_id=execution_id,
            **{k: getattr(fence, k) for k in SCOPE},
            **_expected(receipt, result),
        )
    )
    session.flush()
    for model, rows in zip(
        (StructuredDecision, StructuredOrder, StructuredFill),
        result_graph(fence, result),
        strict=True,
    ):
        for row in rows:
            session.add(model(**row))
        session.flush()
    settle_budget(
        session, fence, reservation.id, charge=amount, outcome="conservative_unknown"
    )
    session.flush()
    return VerifiedStructuredExecution(
        execution_id, binding.snapshot_id, admission_id, receipt.sha256, result
    )
