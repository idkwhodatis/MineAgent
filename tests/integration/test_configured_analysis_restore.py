"""Actual four-unit configured generation, pg_dump/restore, historical result read.

Requires exact installed runtime image proofs and real disposable PostgreSQL.
No mocked process, source, analysis result, snapshot, or backup fallback exists.
Collection is not acceptance. Relocated history never authorizes a new launch.
"""

import os
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

pytestmark = pytest.mark.postgres


def test_actual_configured_analysis_cold_restore_historical_read(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_CONFIGURED_ANALYSIS_REQUIRED") != "1":
        pytest.fail(
            "Actual configured analysis restore requires the four-unit gate",
            pytrace=False,
        )
    import test_backup_restore as legacy
    from rd_configured_controller_fixture import run_configured_generation
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.core.security import get_password_hash
    from backend.app.execution_snapshot_history import (
        read_historical_execution_snapshot,
    )
    from backend.app.factor_analysis_history import read_historical_direct_analysis
    from backend.app.factor_analysis_models import (
        FactorDirectAnalysisIntent,
        FactorDirectAnalysisOutcome,
    )
    from backend.app.models import ExecutionAttempt, ResearchRun, User
    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from backend.app.services import DomainError
    from runtimes.research_supervisor.analysis_supervisor import AnalysisSupervisor
    from workers.research.analysis_process import execute_analysis_acceptance_unit

    source, restored = (
        legacy._engine(drill_databases.source),
        legacy._engine(drill_databases.restored),
    )
    storage, target = (
        tmp_path / "configured-source-storage",
        tmp_path / "configured-restored-storage",
    )
    try:
        legacy._upgrade(source, "027_rd_controller_v3")
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage).model_copy(
            update={"lease_seconds": 3600}
        )
        owner_id = uuid4()
        with Session(source) as session:
            session.add(
                User(
                    id=owner_id,
                    email=f"configured-restore-{owner_id}@example.test",
                    hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                )
            )
            session.commit()
        factory, fence = run_configured_generation(source, settings, owner_id)
        with factory() as session:
            assert session.get(ResearchRun, fence.run_id).status == "succeeded"
            attempt = session.get(ExecutionAttempt, fence.attempt_id)
            assert attempt.status == "succeeded" and attempt.sandbox_stopped is True
            outcomes = session.scalars(
                select(FactorDirectAnalysisOutcome).filter_by(run_id=fence.run_id)
            ).all()
            assert len(outcomes) == 1
            analysis_id = outcomes[0].analysis_id
            snapshot_id = session.get(
                FactorDirectAnalysisIntent, analysis_id
            ).snapshot_id
            admissions = session.scalars(
                select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
            ).all()
            assert len(admissions) == 4
            assert {a.role for a in admissions} == {
                "rd_orchestrator",
                "factor_cutoff_candidate",
                "factor_normalizer",
                "native_evaluator",
            }
            assert all(
                session.get(ResearchProcessState, a.id).status == "stopped"
                for a in admissions
            )
            admission_ids = {a.id for a in admissions}
        original_analysis = read_historical_direct_analysis(
            factory, owner_id, analysis_id, settings
        )
        original_snapshot = read_historical_execution_snapshot(
            factory, owner_id, snapshot_id, settings
        )
        assert original_analysis.result.ic == pytest.approx(
            0.9845785700149436, abs=1e-12
        )
        assert original_analysis.result.rank_ic == pytest.approx(1.0, abs=1e-12)
        assert original_analysis.result.total_pairs == 3
        assert original_analysis.measurement.status == "passed"
        assert original_analysis.measurement.value == pytest.approx(1.0, abs=1e-12)
        assert sorted(r.value for r in original_snapshot.rows) == pytest.approx(
            [-0.02, 0.02, 0.04], abs=1e-12
        )
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        before = source_db.source_snapshot(BackupLimits())
        assert (
            before["research_closure"]["schema_version"]
            == "mineagent.research-restore-closure.v11"
        )
        assert (
            before["rd_source_closure"]["schema_version"]
            == "mineagent.rd-source-restore-closure.v6"
        )
        files = _files(storage)
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == "027_rd_controller_v3"
        assert storage.stat().st_ino != target.stat().st_ino
        assert not (target / ".restore-incomplete").exists()
        assert _files(target) == files
        restored_db = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        )
        after = restored_db.source_snapshot(BackupLimits())
        for key in (
            "research_closure",
            "rd_source_closure",
            "native_closure",
            "execution_closure",
            "direct_analysis_closure",
        ):
            assert after[key] == before[key]
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        from rd_composed_profile_fixture import assert_composed_controller

        with restored_factory() as session:
            assert_composed_controller(session, fence.run_id)
        restored_settings = legacy._note_settings(restored, target).model_copy(
            update={"lease_seconds": 3600}
        )
        assert (
            read_historical_execution_snapshot(
                restored_factory, owner_id, snapshot_id, restored_settings
            )
            == original_snapshot
        )
        assert (
            read_historical_direct_analysis(
                restored_factory, owner_id, analysis_id, restored_settings
            )
            == original_analysis
        )
        # Cache/repeat remains read-only and retains independently established metrics.
        assert (
            read_historical_direct_analysis(
                restored_factory, owner_id, analysis_id, restored_settings
            )
            == original_analysis
        )
        with pytest.raises(DomainError):
            read_historical_direct_analysis(
                restored_factory, uuid4(), analysis_id, restored_settings
            )
        with pytest.raises(DomainError):
            read_historical_execution_snapshot(
                restored_factory, uuid4(), snapshot_id, restored_settings
            )

        def no_launch(*args, **kwargs):
            pytest.fail(
                "Restored historical evidence reached a Docker submission boundary"
            )

        monkeypatch.setattr(AnalysisSupervisor, "verified_inventory", no_launch)
        with pytest.raises(DomainError):
            execute_analysis_acceptance_unit(
                restored_factory, fence, analysis_id, restored_settings
            )
        with restored_factory() as session:
            assert (
                set(
                    session.scalars(
                        select(ResearchProcessAdmission.id).filter_by(
                            run_id=fence.run_id
                        )
                    )
                )
                == admission_ids
            )
        assert _files(target) == files
    finally:
        source.dispose()
        restored.dispose()
