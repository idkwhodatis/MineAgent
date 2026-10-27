"""Required actual configured four-unit gate; no synthetic process/source receipts.

Storage relocation below exercises historical API usability on the same terminal
DB. It is deliberately not labelled a pg_dump/pg_restore acceptance test.
"""

import shutil
from uuid import uuid4

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.postgres


def test_actual_configured_source_snapshot_direct_analysis_generation(
    migrated_engine, settings, owner, other, tmp_path, monkeypatch
):
    from rd_configured_controller_fixture import run_configured_generation

    from backend.app.factor_analysis_history import read_historical_direct_analysis
    from backend.app.factor_analysis_models import FactorDirectAnalysisOutcome
    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.rdagent_models import (
        FactorGenerationOutcome,
        FactorGenerationSource,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from backend.app.services import DomainError
    from runtimes.research_supervisor.analysis_supervisor import AnalysisSupervisor
    from workers.research.analysis_process import execute_analysis_acceptance_unit

    factory, fence = run_configured_generation(migrated_engine, settings, owner.user.id)
    with factory() as session:
        run = session.get(ResearchRun, fence.run_id)
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        assert run.status == attempt.status == "succeeded"
        assert attempt.sandbox_stopped is True
        source = session.scalars(
            select(FactorGenerationSource).filter_by(run_id=fence.run_id)
        ).all()
        generation = session.scalars(
            select(FactorGenerationOutcome).filter_by(run_id=fence.run_id)
        ).all()
        outcomes = session.scalars(
            select(FactorDirectAnalysisOutcome).filter_by(run_id=fence.run_id)
        ).all()
        assert len(source) == len(generation) == len(outcomes) == 1
        assert generation[0].outcome == "accepted"
        outcome = outcomes[0]
        analysis_id = outcome.analysis_id
        assert outcome.measurement["status"] == "passed"
        assert (
            outcome.result["defined_days"] == 1 and outcome.result["total_pairs"] == 3
        )
        admissions = session.scalars(
            select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
        ).all()
        assert sorted(row.role for row in admissions) == [
            "factor_cutoff_candidate",
            "factor_normalizer",
            "native_evaluator",
            "rd_orchestrator",
        ]
        assert all(
            session.get(ResearchProcessState, row.id).status == "stopped"
            for row in admissions
        )
        admission_ids = {row.id for row in admissions}

    def assert_measurement(value):
        # Independently derived Pearson for [.04,.02,-.02] and
        # [110/104-1,104/102-1,96/98-1], NOT a self-hash or any-nonempty result.
        assert value.result.ic == pytest.approx(0.9845785700149436, abs=1e-12)
        assert value.result.rank_ic == pytest.approx(1.0, abs=1e-12)
        assert value.measurement.status == "passed"
        assert value.measurement.value == pytest.approx(1.0, abs=1e-12)

    assert_measurement(
        read_historical_direct_analysis(factory, owner.user.id, analysis_id, settings)
    )
    assert_measurement(
        read_historical_direct_analysis(factory, owner.user.id, analysis_id, settings)
    )
    relocated = tmp_path / "relocated-stopped-storage"
    shutil.copytree(settings.storage_root, relocated)
    restored_settings = settings.model_copy(update={"storage_root": relocated})
    assert settings.storage_root.stat().st_ino != relocated.stat().st_ino
    assert_measurement(
        read_historical_direct_analysis(
            factory, owner.user.id, analysis_id, restored_settings
        )
    )
    with pytest.raises(DomainError):
        read_historical_direct_analysis(
            factory, uuid4(), analysis_id, restored_settings
        )

    # The same actual four-unit result is disclosed through real HTTP/Chromium
    # after relocation, before helper replay assertions. No second generation.
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from tests.integration.engine_result_browser import (
        assert_actual_engine_result_browser,
    )
    from tests.integration.engine_result_http_assertions import (
        assert_actual_engine_result_http,
    )
    from tests.integration.factor_execution_http import assert_factor_execution_http

    assert_actual_engine_result_browser(
        migrated_engine,
        restored_settings,
        owner,
        other,
        factory,
        fence.run_id,
        analysis_id,
        tmp_path,
    )
    assert_factor_execution_http(
        migrated_engine, restored_settings, owner, factory, analysis_id
    )
    with TestClient(
        create_app(settings=restored_settings, engine=migrated_engine)
    ) as http:

        def login(account):
            response = http.post(
                "/api/v1/login/access-token",
                data={
                    "username": account.user.email,
                    "password": account.password,
                },
            )
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        assert_actual_engine_result_http(
            http,
            login(owner),
            login(other),
            factory,
            fence.run_id,
            analysis_id,
            already_released=True,
        )

    def no_launch(*_args, **_kwargs):
        pytest.fail("Terminal historical result must not reach Docker submission")

    monkeypatch.setattr(AnalysisSupervisor, "verified_inventory", no_launch)
    with pytest.raises(DomainError):
        execute_analysis_acceptance_unit(factory, fence, analysis_id, restored_settings)
    with factory() as session:
        assert (
            set(
                session.scalars(
                    select(ResearchProcessAdmission.id).filter_by(run_id=fence.run_id)
                )
            )
            == admission_ids
        )
