"""Actual ten-unit pair, explicit public disclosure, then pg_dump/cold restore.

No fixture rows, stopped receipts, exposure grants or engine results are forged.
Requires the complete 025 migration/runtime/API composition; collection is not
acceptance. This adds ten units, distinct from the existing relocation case.
"""

import os
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

pytestmark = pytest.mark.postgres


def test_actual_structured_pair_cold_restore_disclosure_replay(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_STRUCTURED_ENGINE_REQUIRED") != "1":
        pytest.fail("Actual structured ten-unit restore gate required", pytrace=False)
    import test_backup_restore as legacy
    from fastapi.testclient import TestClient
    from structured_execution_fixture import run_structured_pair
    from structured_release_replay import (
        assert_restored_source_results,
        release_source_results,
        release_structured_results,
    )
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.core.security import get_password_hash
    from backend.app.main import create_app
    from backend.app.models import DataUsageExposure, User
    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.research_process_models import ResearchProcessAdmission
    from backend.app.services import DomainError
    from backend.app.structured_factor_history import (
        read_historical_structured_execution,
    )
    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.research_supervisor.structured_factor_supervisor import (
        StructuredSupervisor,
    )
    from workers.research.structured_process import execute_structured_acceptance_unit

    source = legacy._engine(drill_databases.source)
    restored = legacy._engine(drill_databases.restored)
    storage, target = tmp_path / "structured-source", tmp_path / "structured-restored"
    owner_id, other_id = uuid4(), uuid4()
    password = secrets.token_urlsafe(32)
    emails = {
        owner_id: f"structured-{owner_id}@example.test",
        other_id: f"other-{other_id}@example.test",
    }

    def login(client, identity):
        response = client.post(
            "/api/v1/login/access-token",
            data={"username": emails[identity], "password": password},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def inventory(factory):
        with factory() as session:
            return (
                set(session.scalars(select(ResearchProcessAdmission.id))),
                set(session.scalars(select(DataUsageExposure.id))),
            )

    try:
        legacy._upgrade(source, "025_structured_factor_strategy")
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage).model_copy(
            update={"lease_seconds": 3600}
        )
        with Session(source) as session:
            for identity in (owner_id, other_id):
                session.add(
                    User(
                        id=identity,
                        email=emails[identity],
                        hashed_password=get_password_hash(password),
                    )
                )
            session.commit()
        factory, fence, execution_ids, outputs = run_structured_pair(
            source, settings, owner_id
        )
        originals = tuple(
            read_historical_structured_execution(factory, owner_id, eid, settings)
            for eid in execution_ids
        )
        with TestClient(create_app(settings=settings, engine=source)) as api:
            disclosed = release_source_results(
                api,
                login(api, owner_id),
                login(api, other_id),
                factory,
                tuple(output.snapshot_id for output in outputs),
            )
            structured_disclosed = release_structured_results(
                api,
                login(api, owner_id),
                login(api, other_id),
                fence.run_id,
                execution_ids,
            )
        admission_ids, exposure_ids = inventory(factory)
        assert len(admission_ids) == 10
        assert disclosed.exposure_ids <= exposure_ids
        assert structured_disclosed.exposure_ids <= exposure_ids
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        before = source_db.source_snapshot(BackupLimits())
        files = _files(storage)
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == "025_structured_factor_strategy"
        assert target.stat().st_ino != storage.stat().st_ino
        assert not (target / ".restore-incomplete").exists()
        assert _files(target) == files
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        restored_settings = legacy._note_settings(restored, target).model_copy(
            update={"lease_seconds": 3600}
        )
        restored_db = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        )
        after = restored_db.source_snapshot(BackupLimits())
        closure_keys = {key for key in before if key.endswith("_closure")}
        assert "structured_strategy_closure" in closure_keys
        for key in closure_keys:
            assert after[key] == before[key]
        assert inventory(restored_factory) == (admission_ids, exposure_ids)
        for eid, original in zip(execution_ids, originals, strict=True):
            assert (
                read_historical_structured_execution(
                    restored_factory, owner_id, eid, restored_settings
                )
                == original
            )
            with pytest.raises(DomainError):
                read_historical_structured_execution(
                    restored_factory, other_id, eid, restored_settings
                )
        # Public GET must work with restored release rows BEFORE an idempotent POST.
        with TestClient(create_app(settings=restored_settings, engine=restored)) as api:
            assert_restored_source_results(
                api, login(api, owner_id), login(api, other_id), disclosed
            )
            assert_restored_source_results(
                api, login(api, owner_id), login(api, other_id), structured_disclosed
            )
        assert inventory(restored_factory) == (admission_ids, exposure_ids)

        def no_launch(*_args, **_kwargs):
            pytest.fail("Restored historical evidence reached Docker submission")

        monkeypatch.setattr(StructuredSupervisor, "verified_inventory", no_launch)
        monkeypatch.setattr(StructuredSupervisor, "execute", no_launch)
        for eid in execution_ids:
            with pytest.raises((DomainError, ProcessError)):
                execute_structured_acceptance_unit(
                    restored_factory, fence, eid, restored_settings
                )
        assert inventory(restored_factory) == (admission_ids, exposure_ids)
        assert _files(target) == files
    finally:
        source.dispose()
        restored.dispose()
