"""Real Docker+PG containment after lost identity callback; no fake process proof."""

import pytest
from sqlalchemy import select

from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.rdagent_models import FactorGenerationSource
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from workers.research import rdagent_operational as worker

pytestmark = pytest.mark.postgres


def test_actual_controller_identity_callback_loss_retains_containment(
    migrated_engine, settings, owner, monkeypatch
):
    from rd_controller_fixture import prepare_actual_generation

    factory, fence = prepare_actual_generation(migrated_engine, settings, owner.user.id)

    def callback_lost(*_args, **_kwargs):
        raise RuntimeError("maintained injected identity callback loss")

    monkeypatch.setattr(worker, "record_claimed_controller_identity", callback_lost)
    with pytest.raises(
        RuntimeError, match="maintained injected identity callback loss"
    ):
        worker.execute_maintained_controller(factory, fence, settings)
    with factory() as session:
        admissions = session.scalars(
            select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
        ).all()
        assert len(admissions) == 1 and admissions[0].role == "rd_orchestrator"
        admission = admissions[0]
        assert session.get(ResearchProcessState, admission.id).status == "stopped"
        receipts = session.scalars(
            select(ResearchProcessReceipt).filter_by(admission_id=admission.id)
        ).all()
        assert {record.kind for record in receipts} == {
            "launch_intent",
            "launch_identity",
            "stop",
        }
        assert (
            session.scalar(
                select(FactorGenerationSource).filter_by(run_id=fence.run_id)
            )
            is None
        )
        reservation = session.get(ResearchBudgetReservation, admission.reservation_id)
        settlement = session.scalar(
            select(ResearchBudgetSettlement).filter_by(reservation_id=reservation.id)
        )
        assert reservation.state == "settled"
        assert (
            settlement.outcome == "conservative_unknown"
            and settlement.charge == reservation.amounts
        )
        assert session.get(ResearchRun, fence.run_id).status == "failed"
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        assert attempt.status == "failed" and attempt.sandbox_stopped is True
        assert attempt.exit_code is None  # No returned handle; never invent PID1 exit.
    assert (
        worker.recover_controller_unit(factory, settings.storage_root, admission.id)
        is True
    )
