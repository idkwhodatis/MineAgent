"""Explicit new composed controller gate; historical v2 fixtures stay unchanged."""

from sqlalchemy import select

PROFILE = "mineagent.rdagent-orchestrator.v3"
REVISION = "027c_seeded_controller_v4"
SEEDED_PROFILE = "mineagent.rdagent-orchestrator.v4"
SEEDED_CONTROLLER_WALL_MS = 180000
SEEDED_COMPOSED_WALL_MS = 360000
CONTROLLER_WALL_MS = 120000
COMPOSED_WALL_MS = 300000


def assert_composed_controller(session, run_id, *, seeded=False):
    """Read real persisted config/admission/hold, never fabricate a runtime proof."""
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorControllerLaunch
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from backend.app.research_process_models import ResearchProcessAdmission

    assert type(seeded) is bool
    expected_profile = SEEDED_PROFILE if seeded else PROFILE
    expected_wall = SEEDED_CONTROLLER_WALL_MS if seeded else CONTROLLER_WALL_MS
    run = session.get(ResearchRun, run_id)
    assert run.config["controller_profile"] == expected_profile
    assert run.config["max_iterations"] == 1
    expected_floor = SEEDED_COMPOSED_WALL_MS if seeded else COMPOSED_WALL_MS
    binding = run.config.get("model_binding")
    if binding is not None:
        expected_floor += 32 * binding["request_deadline_ms"]
    assert run.config["budget"]["wall_milliseconds"] == expected_floor
    admission = session.scalars(
        select(ResearchProcessAdmission).filter_by(
            run_id=run_id, role="rd_orchestrator"
        )
    ).one()
    assert admission.profile == expected_profile
    reservation = session.get(ResearchBudgetReservation, admission.reservation_id)
    assert reservation.purpose == "process_unit"
    assert reservation.run_id == run_id
    assert reservation.amounts["wall_milliseconds"] == expected_wall
    if seeded:
        assert reservation.state == "settled"
        settled = session.scalars(
            select(ResearchBudgetSettlement).filter_by(reservation_id=reservation.id)
        ).one()
        assert settled.run_id == run_id
        assert settled.charge == reservation.amounts
        assert settled.charge["wall_milliseconds"] == SEEDED_CONTROLLER_WALL_MS
        assert settled.outcome == "conservative_unknown"
    launch = session.scalars(
        select(FactorControllerLaunch).filter_by(run_id=run_id)
    ).one()
    assert launch.admission_id == admission.id
    assert launch.bootstrap["config"]["controller_profile"] == expected_profile

    children = session.scalars(
        select(ResearchProcessAdmission).where(
            ResearchProcessAdmission.run_id == run_id,
            ResearchProcessAdmission.role != "rd_orchestrator",
        )
    ).all()
    assert {child.role: child.profile for child in children} == {
        "factor_cutoff_candidate": "mineagent.factor-prefix-candidate.v2",
        "factor_normalizer": "mineagent.factor-numeric-hdf.v1",
        "native_evaluator": "mineagent.qlib-direct-analysis.v1",
    }
    assert len(children) == 3
    for child in children:
        held = session.get(ResearchBudgetReservation, child.reservation_id)
        assert held.run_id == run_id and held.purpose == "process_unit"
        assert held.amounts["wall_milliseconds"] == 60000
