"""027 actual protected result publication; no outcome from targets or failure.

The fixed engine reader validates native accounting. A measurement is only a
projection of those fresh bytes, never a substitute for provider or stop proof.
"""

from dataclasses import dataclass, fields
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.oe_structured_engine_services import (
    SCOPE,
    fail,
    require_execution,
    require_parent_execution,
)
from backend.app.oe_structured_execution_input import resolve_execution_input
from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.structured_process_services import (
    StoppedStructuredReceipt,
    StructuredOutputReceipt,
    _completed_accounting,
    _persisted,
    read_structured_output,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.objective import (
    StructuredMeasurement,
    project_result,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical
from packages.structured_factor_strategy.wire import (
    StructuredResult,
    read_input,
    read_result,
)
from runtimes.research_supervisor.structured_factor_supervisor import check_input_bytes
from runtimes.structured_factor_engine.journal import StructuredIntent


@dataclass(frozen=True)
class VerifiedCandidateEngineOutcome:
    execution_id: UUID
    candidate_id: UUID
    admission_id: UUID
    result_sha256: str
    result: StructuredResult
    measurement: StructuredMeasurement


def _read(session, fence, execution_id, admission_id, settings, *, peer):
    pins = require_execution(session, fence, execution_id, settings, peer=peer)
    value = _read_physical(session, fence, pins, admission_id, settings)
    if require_execution(session, fence, execution_id, settings, peer=peer) != pins:
        fail("oe_structured_engine_result_drift")
    return value


def _read_physical(session, fence, pins, admission_id, settings):
    """Shared physical validation, called only within explicit guarded readers."""
    execution_id = pins.execution_id
    receipt, raw = read_structured_output(
        session, fence, admission_id, storage_root=settings.storage_root
    )
    _completed_accounting(session, fence, receipt.admission)
    binding = receipt.inputs
    if (
        binding.execution_id,
        binding.structured_strategy_version_id,
        binding.snapshot_id,
        binding.strategy_input_sha256,
        binding.strategy_input_size_bytes,
        binding.market_sha256,
        binding.market_size_bytes,
        receipt.admission.logical_operation_id,
        receipt.admission.image_id,
    ) != (
        execution_id,
        pins.structured_strategy_version_id,
        pins.snapshot_id,
        pins.strategy_input_sha256,
        pins.strategy_input_size_bytes,
        pins.market_sha256,
        pins.market_size_bytes,
        execution_id,
        pins.engine_image_id,
    ):
        fail("oe_structured_engine_stopped_binding")
    strategy, market = resolve_execution_input(session, fence, execution_id, settings)
    _, _, receipts = _persisted(session, admission_id)
    intent = StructuredIntent.model_validate_json(
        canonical(receipts["launch_intent"].payload)
    )
    check_input_bytes(
        intent, {"/input/strategy.json": strategy, "/input/market.json": market}
    )
    result = read_result(raw, read_input(strategy))
    if raw != canonical_bytes(result) or digest(result) != receipt.sha256:
        fail("oe_structured_engine_result_canonical")
    measurement = project_result(strategy, raw, pins.objective)
    stopped = StoppedStructuredReceipt(
        **{f.name: getattr(receipt, f.name) for f in fields(StructuredOutputReceipt)},
        result_bytes=raw,
    )
    return pins, stopped, result, measurement


def _expected(pins, receipt, measurement):
    return {
        "execution_id": pins.execution_id,
        "candidate_id": pins.candidate_id,
        "admission_id": receipt.admission.id,
        "stop_receipt_id": receipt.stop_receipt_id,
        "stop_receipt_kind": "stop",
        "stop_receipt_sha256": receipt.stop_receipt_hash,
        "extraction_receipt_id": receipt.extraction_receipt_id,
        "extraction_receipt_kind": "extraction",
        "extraction_receipt_sha256": receipt.extraction_receipt_hash,
        "result_canonical": receipt.result_bytes.decode("ascii"),
        "result_sha256": receipt.sha256,
        "result_size_bytes": receipt.size_bytes,
        "measurement_canonical": canonical_bytes(measurement).decode("ascii"),
        "measurement_sha256": digest(measurement),
        "objective_sha256": digest(pins.objective),
    }


def _outcome(session, execution_id):
    from backend.app.oe_structured_models import OEStructuredEngineOutcome

    return session.scalar(
        select(OEStructuredEngineOutcome).filter_by(execution_id=execution_id)
    )


def read_candidate_engine_outcome(session, fence, execution_id, settings, *, peer):
    previous = _outcome(session, execution_id)
    if previous is None or any(
        getattr(previous, key) != getattr(fence, key) for key in SCOPE
    ):
        fail("oe_structured_engine_outcome_scope")
    pins, receipt, result, measurement = _read(
        session, fence, execution_id, previous.admission_id, settings, peer=peer
    )
    if any(
        (
            getattr(previous, key) != value
            for key, value in _expected(pins, receipt, measurement).items()
        )
    ):
        fail("oe_structured_engine_outcome_drift")
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    if reservation.state != "settled":
        fail("oe_structured_engine_outcome_unsettled")
    return VerifiedCandidateEngineOutcome(
        execution_id,
        pins.candidate_id,
        receipt.admission.id,
        receipt.sha256,
        result,
        measurement,
    )


def persist_candidate_engine_outcome(
    session, fence, execution_id, admission_id, settings, *, peer
):
    """Flush-only exact outcome and whole conservative charge in caller transaction."""
    from backend.app.oe_structured_models import OEStructuredEngineOutcome
    from backend.app.research_broker_services import settle_budget

    pins, receipt, result, measurement = _read(
        session, fence, execution_id, admission_id, settings, peer=peer
    )
    previous = _outcome(session, execution_id)
    if previous is not None:
        if previous.admission_id != admission_id:
            fail("oe_structured_engine_outcome_admission")
        return read_candidate_engine_outcome(
            session, fence, execution_id, settings, peer=peer
        )
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    session.add(
        OEStructuredEngineOutcome(
            id=uuid4(),
            **{key: getattr(fence, key) for key in SCOPE},
            **_expected(pins, receipt, measurement),
        )
    )
    settle_budget(
        session,
        fence,
        reservation.id,
        charge=BudgetVector.model_validate(reservation.amounts),
        outcome="conservative_unknown",
    )
    session.flush()
    return VerifiedCandidateEngineOutcome(
        execution_id,
        pins.candidate_id,
        admission_id,
        receipt.sha256,
        result,
        measurement,
    )


def read_parent_engine_outcome(
    session, fence, parent_candidate_id, settings, *, peer, current_intent_id
):
    """Read-only parent evidence under current intent; no old peer or new unit."""
    pins = require_parent_execution(
        session,
        fence,
        parent_candidate_id,
        settings,
        peer=peer,
        current_intent_id=current_intent_id,
    )
    previous = _outcome(session, pins.execution_id)
    if previous is None or any(
        getattr(previous, key) != getattr(fence, key) for key in SCOPE
    ):
        fail("oe_structured_parent_outcome_scope")
    _, receipt, result, measurement = _read_physical(
        session, fence, pins, previous.admission_id, settings
    )
    if any(
        getattr(previous, key) != value
        for key, value in _expected(pins, receipt, measurement).items()
    ):
        fail("oe_structured_parent_outcome_drift")
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    if reservation.state != "settled":
        fail("oe_structured_parent_unsettled")
    after = require_parent_execution(
        session,
        fence,
        parent_candidate_id,
        settings,
        peer=peer,
        current_intent_id=current_intent_id,
    )
    if after != pins:
        fail("oe_structured_parent_changed")
    return VerifiedCandidateEngineOutcome(
        pins.execution_id,
        parent_candidate_id,
        previous.admission_id,
        receipt.sha256,
        result,
        measurement,
    )


def read_population_engine_outcome(
    session, fence, candidate_id, settings, *, peer, current_intent_id
):
    """Fresh actual completed result of a member of current BEFORE sampling state."""
    from backend.app.oe_structured_engine_services import require_population_execution

    pins = require_population_execution(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_intent_id=current_intent_id,
    )
    previous = _outcome(session, pins.execution_id)
    if previous is None or any(
        getattr(previous, key) != getattr(fence, key) for key in SCOPE
    ):
        fail("oe_structured_population_outcome_scope")
    _, receipt, result, measurement = _read_physical(
        session, fence, pins, previous.admission_id, settings
    )
    if any(
        getattr(previous, key) != value
        for key, value in _expected(pins, receipt, measurement).items()
    ):
        fail("oe_structured_population_outcome_drift")
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    if reservation.state != "settled":
        fail("oe_structured_population_unsettled")
    if (
        require_population_execution(
            session,
            fence,
            candidate_id,
            settings,
            peer=peer,
            current_intent_id=current_intent_id,
        )
        != pins
    ):
        fail("oe_structured_population_changed")
    return VerifiedCandidateEngineOutcome(
        pins.execution_id,
        candidate_id,
        previous.admission_id,
        receipt.sha256,
        result,
        measurement,
    )


def read_sampling_engine_outcome(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Actual completed member of latest seed/applied cursor, before sampling."""
    from backend.app.oe_structured_engine_services import require_sampling_execution

    pins = require_sampling_execution(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_control_id=current_control_id,
    )
    previous = _outcome(session, pins.execution_id)
    if previous is None or any(
        getattr(previous, key) != getattr(fence, key) for key in SCOPE
    ):
        fail("oe_structured_sampling_outcome_scope")
    _, receipt, result, measurement = _read_physical(
        session, fence, pins, previous.admission_id, settings
    )
    if any(
        getattr(previous, key) != value
        for key, value in _expected(pins, receipt, measurement).items()
    ):
        fail("oe_structured_sampling_outcome_drift")
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    if reservation.state != "settled":
        fail("oe_structured_sampling_unsettled")
    if (
        require_sampling_execution(
            session,
            fence,
            candidate_id,
            settings,
            peer=peer,
            current_control_id=current_control_id,
        )
        != pins
    ):
        fail("oe_structured_sampling_changed")
    return VerifiedCandidateEngineOutcome(
        pins.execution_id,
        candidate_id,
        previous.admission_id,
        receipt.sha256,
        result,
        measurement,
    )


def read_terminal_engine_outcome(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Actual stopped result at the current controller terminal boundary."""
    from backend.app.oe_structured_engine_services import require_terminal_execution

    pins = require_terminal_execution(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_control_id=current_control_id,
    )
    previous = _outcome(session, pins.execution_id)
    if previous is None or any(
        getattr(previous, key) != getattr(fence, key) for key in SCOPE
    ):
        fail("oe_structured_terminal_outcome_scope")
    _, receipt, result, measurement = _read_physical(
        session, fence, pins, previous.admission_id, settings
    )
    if any(
        getattr(previous, key) != value
        for key, value in _expected(pins, receipt, measurement).items()
    ):
        fail("oe_structured_terminal_outcome_drift")
    reservation = session.get(
        ResearchBudgetReservation, receipt.admission.reservation_id
    )
    if reservation.state != "settled":
        fail("oe_structured_terminal_unsettled")
    if (
        require_terminal_execution(
            session,
            fence,
            candidate_id,
            settings,
            peer=peer,
            current_control_id=current_control_id,
        )
        != pins
    ):
        fail("oe_structured_terminal_changed")
    return VerifiedCandidateEngineOutcome(
        pins.execution_id,
        candidate_id,
        previous.admission_id,
        receipt.sha256,
        result,
        measurement,
    )
