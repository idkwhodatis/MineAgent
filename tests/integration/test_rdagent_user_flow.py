"""Required real HTTP→outbox→Procrastinate→four protected units→result test.

Uses disposable DATABASES, not a connection-only search_path override: the normal
worker opens its own independent DB connections and must see the same real app.
No service shortcut, synthetic attempt, model-provider charge or Docker fallback.
"""

import os
import secrets
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from rd_composed_profile_fixture import PROFILE, assert_composed_controller
from rd_configured_dataset_fixture import persist_configured_dataset
from rd_rpc_diagnostic import diagnose_rd_queue
from rd_submission_browser import create_queued_submission_browser
from rd_submission_fixture import body, login, profile
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa:PLC0414

pytestmark = pytest.mark.postgres


def test_authenticated_rd_queue_four_unit_declared_objective_and_exact_replay(
    drill_databases, tmp_path
):
    if os.environ.get("MINEAGENT_RD_USER_FLOW_REQUIRED") != "1":
        pytest.fail(
            "Actual RD user flow requires its explicit four-image gate", pytrace=False
        )
    import test_backup_restore as legacy
    from engine_result_browser import assert_actual_engine_result_browser

    from backend.app.core.security import get_password_hash
    from backend.app.factor_analysis_models import FactorDirectAnalysisOutcome
    from backend.app.main import create_app
    from backend.app.models import ExecutionAttempt, ResearchRun, User
    from backend.app.rdagent_submission_models import FactorResearchSubmission
    from backend.app.rdagent_submission_receipts import read_request
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app
    from workers.research.rdagent_queue import TASK_NAME

    engine = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(engine, "027b_provider_material")
        storage = tmp_path / "rd-user-flow"
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(engine, storage).model_copy(
            update={
                "rdagent_research_enabled": True,
                "rdagent_runtime_profile": profile(
                    actual_images=True, controller_profile=PROFILE
                ),
                "lease_seconds": 120,
                "heartbeat_seconds": 1,
            }
        )
        factory = sessionmaker(engine, expire_on_commit=False)
        password = secrets.token_urlsafe(32)
        user = User(
            id=uuid4(),
            email=uuid4().hex + "@example.com",
            hashed_password=get_password_hash(password),
            full_name="Actual RD user-flow fixture",
        )
        other = User(
            id=uuid4(),
            email=uuid4().hex + "@example.com",
            hashed_password=get_password_hash(password),
            full_name="Other actual browser account",
        )
        with factory.begin() as session:
            session.add_all([user, other])
        dataset, _source, policy = persist_configured_dataset(
            factory, user.id, settings
        )
        request = body(dataset, policy, controller_profile=PROFILE)
        account = SimpleNamespace(user=user, password=password)
        create_queued_submission_browser(settings, engine, account, dataset, request)
        with factory() as session:
            saved = session.scalars(
                select(FactorResearchSubmission).filter_by(
                    owner_id=user.id, request_sha256=request.sha256
                )
            ).one()
            run_id, key = saved.run_id, saved.idempotency_key
            exact = {
                **read_request(saved).model_dump(mode="json"),
                "preflight_sha256": saved.preflight_sha256,
            }
            assert session.get(ResearchRun, run_id).status == "queued"
        with TestClient(create_app(settings=settings, engine=engine)) as client:
            headers = login(client, SimpleNamespace(user=user, password=password))
            capabilities = client.get(
                "/api/v1/engine-runs/capabilities", headers=headers
            )
            assert (
                capabilities.status_code == 200
                and capabilities.json()["engines"][0]["can_create"]
            )
            with factory.begin() as session:
                assert (
                    session.scalar(select(func.count()).select_from(ExecutionAttempt))
                    == 0
                )
                assert dispatch_outbox(session, settings=settings) == 1
            queue = create_queue_app(settings)
            with diagnose_rd_queue(factory, run_id), queue.open():
                queue.run_worker(
                    queues=["research"],
                    concurrency=1,
                    wait=False,
                    install_signal_handlers=False,
                    delete_jobs="never",
                )
            with factory() as session:
                run = session.get(ResearchRun, run_id)
                assert run.status == "succeeded"
                attempts = session.scalars(
                    select(ExecutionAttempt).filter_by(run_id=run_id)
                ).all()
                assert len(attempts) == 1 and attempts[0].sandbox_stopped is True
                admissions = session.scalars(
                    select(ResearchProcessAdmission).filter_by(run_id=run_id)
                ).all()
                assert_composed_controller(session, run_id)
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
                measured = session.scalar(
                    select(FactorDirectAnalysisOutcome).filter_by(run_id=run_id)
                )
                assert measured.measurement["status"] == "passed"
                assert measured.result["rank_ic"] == 1.0
                assert measured.result["ic"] == pytest.approx(0.9845785700149436)
                jobs = session.execute(
                    text("SELECT task_name,status FROM procrastinate_jobs")
                ).all()
                assert jobs == [(TASK_NAME, "succeeded")]
            detail = client.get("/api/v1/engine-runs/" + str(run_id), headers=headers)
            history = client.get(
                "/api/v1/engine-runs/" + str(run_id) + "/history", headers=headers
            )
            assert (
                detail.status_code == history.status_code == 200
                and detail.json()["status"] == "succeeded"
            )
            settings.rdagent_research_enabled = False
            repeated = client.post(
                "/api/v1/engine-runs",
                headers={**headers, "Idempotency-Key": key},
                json=exact,
            )
            assert (
                repeated.status_code == 201
                and repeated.json()["replayed"]
                and repeated.json()["status"] == "succeeded"
            )
            with factory() as session:
                assert (
                    session.scalar(
                        select(func.count()).select_from(ResearchProcessAdmission)
                    )
                    == 4
                )
        assert_actual_engine_result_browser(
            engine,
            settings,
            account,
            SimpleNamespace(user=other, password=password),
            factory,
            run_id,
            measured.analysis_id,
            tmp_path,
        )
        # Real stopped user-created run, including its immutable request receipt,
        # survives cold restore. Relocated journals are historical, never grants.
        from test_factor_restore import _files
        from test_neutral_restore import _roundtrip

        from backend.app.operations_backup import BackupLimits, PostgresDatabase

        before = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        assert (
            before["research_closure"]["schema_version"]
            == "mineagent.research-restore-closure.v11"
        )
        assert (
            before["rd_source_closure"]["schema_version"]
            == "mineagent.rd-source-restore-closure.v6"
        )
        files = _files(storage)
        target = tmp_path / "restored-user-flow"
        manifest = _roundtrip(drill_databases, tmp_path, engine, storage, target)
        assert (
            manifest["schema_revision"] == "027b_provider_material"
            and _files(target) == files
        )
        after = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        for name in (
            "research_closure",
            "rd_source_closure",
            "native_closure",
            "execution_closure",
            "direct_analysis_closure",
            "rd_submission_closure",
        ):
            assert before[name] == after[name]
        restored = legacy._engine(drill_databases.restored)
        try:
            with sessionmaker(restored, expire_on_commit=False)() as session:
                assert_composed_controller(session, run_id)
            restored_settings = legacy._note_settings(restored, target)
            with TestClient(
                create_app(settings=restored_settings, engine=restored)
            ) as client:
                headers = login(client, account)
                repeated = client.post(
                    "/api/v1/engine-runs",
                    headers={**headers, "Idempotency-Key": key},
                    json=exact,
                )
                assert (
                    repeated.status_code == 201
                    and repeated.json()["replayed"]
                    and repeated.json()["status"] == "succeeded"
                )
        finally:
            restored.dispose()
    finally:
        engine.dispose()
