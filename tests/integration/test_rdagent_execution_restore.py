"""Real stopped-controller/snapshot cold restore; no precursor substitutions.

Requires the composed operational controller fixture, three exact Docker images,
actual PostgreSQL, pg_dump/pg_restore and disposable database creation authority.
Collection is not acceptance; no SQLite or mocked process fallback exists.
"""

import os
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414 - pytest fixture export

pytestmark = pytest.mark.postgres


def test_actual_stopped_controller_execution_snapshot_cold_restore(
    drill_databases, tmp_path
):
    if os.environ.get("MINEAGENT_RD_CONTROLLER_REQUIRED") != "1":
        pytest.fail(
            "Opted-in actual execution restore requires the real controller gate",
            pytrace=False,
        )
    import test_backup_restore as legacy
    from rd_controller_fixture import run_actual_generation
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.core.security import get_password_hash
    from backend.app.models import ExecutionAttempt, ResearchRun, User
    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )

    source, restored = (
        legacy._engine(drill_databases.source),
        legacy._engine(drill_databases.restored),
    )
    storage, target = (
        tmp_path / "actual-source-storage",
        tmp_path / "actual-restored-storage",
    )
    try:
        legacy._upgrade(source, "021_execution_snapshots")
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage).model_copy(
            update={"lease_seconds": 3600}
        )
        owner_id = uuid4()
        with Session(source) as session:
            session.add(
                User(
                    id=owner_id,
                    email=f"actual-restore-{owner_id}@example.test",
                    hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                )
            )
            session.commit()
        _factory, fence = run_actual_generation(source, settings, owner_id)
        with Session(source) as session:
            assert session.get(ResearchRun, fence.run_id).status == "succeeded"
            attempt = session.get(ExecutionAttempt, fence.attempt_id)
            assert attempt.status == "succeeded" and attempt.sandbox_stopped is True
            states = session.scalars(
                select(ResearchProcessState)
                .join(
                    ResearchProcessAdmission,
                    ResearchProcessAdmission.id == ResearchProcessState.admission_id,
                )
                .where(ResearchProcessAdmission.run_id == fence.run_id)
            ).all()
            assert len(states) == 3 and {state.status for state in states} == {
                "stopped"
            }
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        before = source_db.source_snapshot(BackupLimits())
        closures = (
            "research_closure",
            "rd_source_closure",
            "native_closure",
            "execution_closure",
        )
        assert (
            len(
                before["execution_closure"]["tables"][
                    "factor_execution_snapshot_outcomes"
                ]
            )
            == 1
        )
        assert (
            len(
                before["execution_closure"]["tables"][
                    "factor_execution_evaluation_outcomes"
                ]
            )
            == 1
        )
        assert (
            len(before["rd_source_closure"]["tables"]["factor_generation_outcomes"])
            == 1
        )
        files = _files(storage)
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == "021_execution_snapshots"
        assert _files(target) == files
        restored_db = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        )
        after = restored_db.source_snapshot(BackupLimits())
        for key in closures:
            assert after[key] == before[key]
        # Restored stopped rows are historical evidence, never live resumable grants.
        with Session(restored) as session:
            assert session.get(ResearchRun, fence.run_id).status == "succeeded"
            assert (
                session.get(ExecutionAttempt, fence.attempt_id).sandbox_stopped is True
            )
    finally:
        source.dispose()
        restored.dispose()
