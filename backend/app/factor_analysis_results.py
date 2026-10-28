"""Persist actual stopped direct-analysis measurements under fresh fenced scope.

The protected analysis output remains opaque canonical JSON charged to its
process reservation. No second writer, caller result DTO, host Qlib invocation,
refund, implicit commit or successful-adoption claim is introduced.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorDirectAnalysisOutcome
from backend.app.factor_analysis_services import (
    SCOPE,
    _error,
    _materialize_with_snapshot,
    _plan,
    require_analysis_plan,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.contracts import DirectResult
from packages.qlib_adapter.execution_binding import DirectAnalysisProjection
from packages.qlib_adapter.objective import (
    DirectObjectiveMeasurement,
    measure_direct_objective,
)
from packages.research_runtime.budget_contracts import BudgetVector


@dataclass(frozen=True)
class VerifiedDirectAnalysis:
    analysis_id: UUID
    snapshot_id: UUID
    admission_id: UUID
    result_sha256: str
    measurement_sha256: str
    result: DirectResult
    measurement: DirectObjectiveMeasurement
    qualification: str = "current_stopped_measurement_not_adoption_authority"


def _measured_with_snapshot(session, fence, analysis_id, admission_id, settings):
    from backend.app.analysis_process_services import read_stopped_analysis

    binding = require_analysis_plan(session, fence, analysis_id)
    receipt, result = read_stopped_analysis(session, fence, admission_id, settings)
    if receipt.analysis_id != analysis_id or receipt.admission.id != admission_id:
        raise _error()
    _, objective = _plan(session, fence)
    # Keep the second fresh physical read after stopped-result validation.
    # Return this internally produced snapshot, never accept a detached one.
    _, _, value, _, snapshot = _materialize_with_snapshot(
        session, fence, binding.snapshot_id, settings
    )
    projection = DirectAnalysisProjection(
        value.protocol, value.factors, value.labels, value.calendar_sessions
    )
    measurement = measure_direct_objective(objective, projection, result)
    raw = canonical_bytes(result)
    if (
        receipt.result_bytes != raw
        or receipt.sha256 != digest(result)
        or receipt.size_bytes != len(raw)
    ):
        raise _error()
    return binding, receipt, result, measurement, snapshot


def _measured(session, fence, analysis_id, admission_id, settings):
    return _measured_with_snapshot(session, fence, analysis_id, admission_id, settings)[
        :4
    ]


def _reservation(session, fence, receipt, *, completed):
    row = session.get(ResearchBudgetReservation, receipt.admission.reservation_id)
    if (
        row is None
        or any(getattr(row, k) != getattr(fence, k) for k in SCOPE)
        or row.purpose != "process_unit"
    ):
        raise _error()
    amount = BudgetVector.model_validate(row.amounts)
    if not amount.covers(
        BudgetVector(
            process_slots=1,
            evaluator_executions=1,
            wall_milliseconds=60000,
            storage_bytes=67174400,
        )
    ):
        raise _error()
    settlement = session.scalar(
        select(ResearchBudgetSettlement).filter_by(reservation_id=row.id)
    )
    if row.state == "settled":
        if (
            settlement is None
            or settlement.charge != row.amounts
            or settlement.outcome != "conservative_unknown"
        ):
            raise _error()
    elif (
        completed
        or row.state not in {"reserved", "uncertain"}
        or settlement is not None
    ):
        raise _error()
    return row, amount


def _expected(receipt, result, measurement):
    return {
        "admission_id": receipt.admission.id,
        "launch_identity_receipt_id": receipt.launch_identity_receipt_id,
        "launch_identity_receipt_sha256": receipt.launch_identity_receipt_hash,
        "launch_identity_kind": "launch_identity",
        "stop_receipt_id": receipt.stop_receipt_id,
        "stop_receipt_sha256": receipt.stop_receipt_hash,
        "stop_kind": "stop",
        "extraction_receipt_id": receipt.extraction_receipt_id,
        "extraction_receipt_sha256": receipt.extraction_receipt_hash,
        "extraction_kind": "extraction",
        "result": result.model_dump(mode="json"),
        "result_canonical": receipt.result_bytes.decode("ascii"),
        "result_sha256": receipt.sha256,
        "result_size_bytes": receipt.size_bytes,
        "measurement": measurement.model_dump(mode="json"),
        "measurement_canonical": canonical_bytes(measurement).decode("ascii"),
        "measurement_sha256": digest(measurement),
    }


def _read_verified_analysis_with_snapshot(session, fence, analysis_id, settings):
    # Metadata scope is required before any row can be used to choose a receipt.
    require_analysis_plan(session, fence, analysis_id)
    row = session.get(FactorDirectAnalysisOutcome, analysis_id)
    if row is None or any(getattr(row, k) != getattr(fence, k) for k in SCOPE):
        raise _error()
    binding, receipt, result, measurement, snapshot = _measured_with_snapshot(
        session, fence, analysis_id, row.admission_id, settings
    )
    if any(
        getattr(row, k) != v for k, v in _expected(receipt, result, measurement).items()
    ):
        raise _error()
    _reservation(session, fence, receipt, completed=True)
    return snapshot, VerifiedDirectAnalysis(
        analysis_id,
        binding.snapshot_id,
        row.admission_id,
        row.result_sha256,
        row.measurement_sha256,
        result,
        measurement,
    )


def read_verified_direct_analysis(session, fence, analysis_id, settings):
    return _read_verified_analysis_with_snapshot(session, fence, analysis_id, settings)[
        1
    ]


def persist_direct_analysis_outcome(
    session, fence, analysis_id, admission_id, settings
):
    """Flush-only immutable result+full conservative charge in one transaction."""
    from backend.app.research_broker_services import settle_budget

    binding, receipt, result, measurement = _measured(
        session, fence, analysis_id, admission_id, settings
    )
    previous = session.get(FactorDirectAnalysisOutcome, analysis_id)
    if previous is not None:
        if previous.admission_id != admission_id:
            raise _error()
        return read_verified_direct_analysis(session, fence, analysis_id, settings)
    reservation, amount = _reservation(session, fence, receipt, completed=False)
    session.add(
        FactorDirectAnalysisOutcome(
            analysis_id=analysis_id,
            **{k: getattr(fence, k) for k in SCOPE},
            **_expected(receipt, result, measurement),
        )
    )
    session.flush()
    settle_budget(
        session, fence, reservation.id, charge=amount, outcome="conservative_unknown"
    )
    session.flush()
    return VerifiedDirectAnalysis(
        analysis_id,
        binding.snapshot_id,
        admission_id,
        receipt.sha256,
        digest(measurement),
        result,
        measurement,
    )


def analysis_feedback_fields(verified):
    """Projection only: caller must have freshly obtained the typed host result.

    This deliberately grants no admission or publication capability and does not
    manufacture an adoption decision from a declaration or caller boolean.
    """
    if type(verified) is not VerifiedDirectAnalysis:
        raise ValueError("Exact host analysis result type required")
    return {
        "analysis_id": verified.analysis_id,
        "analysis_result_sha256": verified.result_sha256,
        "objective_measurement_sha256": verified.measurement_sha256,
        "objective_status": verified.measurement.status,
        "objective_reason": verified.measurement.reason,
    }
