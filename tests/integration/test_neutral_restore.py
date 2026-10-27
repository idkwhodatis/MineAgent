"""Real PostgreSQL cold restore and genuine historical upgrade for N1.

Internal fixture records exercise the closed neutral primitives attached to a
legacy run. They are not engine admission or proof that an upstream engine ran.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
import test_backup_restore as legacy
import test_factor_restore as factors
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from test_backup_restore import drill_databases as shared_drill_databases

drill_databases = shared_drill_databases
pytestmark = pytest.mark.postgres
HEAD = "021_execution_snapshots"


def _required_image():
    image = os.environ.get("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE")
    if not image:
        pytest.fail(
            "Real neutral restore requires a pinned supervisor image", pytrace=False
        )
    return image


def _settings(engine, storage):
    from backend.app.core.atomic_storage import provision_directory

    provision_directory(storage / "research-objects", durable_parent=storage)
    return legacy._note_settings(engine, storage).model_copy(
        update={"lease_seconds": 3600}
    )


def _new_fence(engine, settings):
    from backend.app.models import DatasetVersion, ResearchRun, StrategyVersion
    from backend.app.services import claim_attempt
    from packages.research_runtime.broker_contracts import digest
    from packages.research_runtime.fences import AttemptFence

    with Session(engine, expire_on_commit=False) as session:
        source = session.scalar(
            select(ResearchRun).where(
                ResearchRun.idempotency_key == "historical-cold-restore"
            )
        )
        dataset = session.get(DatasetVersion, source.dataset_version_id)
        strategy = session.get(StrategyVersion, source.strategy_version_id)
        config = {
            "task": strategy.task,
            "dataset_version_id": str(dataset.id),
            "strategy_version_id": str(strategy.id),
            "start_date": str(dataset.start_date),
            "end_date": str(dataset.end_date),
            "profile": "corrected_explicit_config",
            "market": strategy.task[:2].upper(),
            "dataset_sha256": dataset.content_sha256,
            "strategy_sha256": strategy.content_sha256,
            "execution_mode": "seed_evaluation",
            "is_synthetic": dataset.is_synthetic,
        }
        run = ResearchRun(
            id=uuid4(),
            owner_id=source.owner_id,
            idempotency_key=f"neutral-restore-{uuid4().hex}",
            request_sha256=digest(config),
            config=config,
            dataset_version_id=dataset.id,
            strategy_version_id=strategy.id,
            purpose="legacy_strategy",
            engine="legacy",
            status="queued",
            generation=1,
        )
        session.add(run)
        session.flush()
        attempt = claim_attempt(session, run.id, 1, "neutral-restore-fixture", settings)
        assert attempt is not None
        result = AttemptFence(run.owner_id, run.id, attempt.id, 1, attempt.lease_token)
        session.commit()
    return result


def _initialize(factory, fence):
    from backend.app.research_broker_services import initialize_budget
    from backend.app.research_lifecycle import neutral_transaction
    from packages.research_runtime.budget_contracts import BudgetVector

    with neutral_transaction(factory, fence) as session:
        initialize_budget(
            session,
            fence,
            BudgetVector(
                calls=100,
                tokens=100000,
                storage_bytes=16 * 1024**2,
                process_slots=10,
                wall_milliseconds=600000,
            ),
        )


def _publish_request(factory, fence, settings):
    from packages.research_runtime.broker_contracts import ChatRequest, Message
    from workers.research.model_broker import publish_model_object

    request = ChatRequest(
        messages=(
            Message(role="user", content=f"neutral restore fixture {fence.run_id}"),
        ),
        max_output_tokens=32,
    )
    return request, publish_model_object(
        factory, fence, "model_request", request, settings
    )


def _finish(engine, fence):
    from backend.app.services import finish_attempt

    with Session(engine) as session:
        # No legacy evaluator was launched: this internal primitive-only fixture
        # deliberately ends failed rather than claiming a successful strategy.
        finish_attempt(
            session,
            fence.attempt_id,
            fence.generation,
            fence.lease_token,
            {"status": "failed", "error": "Internal neutral restore fixture completed"},
            sandbox_stopped=True,
        )
        session.commit()


def _publish_after_restore(engine, storage):
    settings = _settings(engine, storage)
    factory = sessionmaker(engine, expire_on_commit=False)
    fence = _new_fence(engine, settings)
    _initialize(factory, fence)
    _, ref = _publish_request(factory, fence, settings)
    _finish(engine, fence)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT object_kind FROM research_objects WHERE id=:id"),
                {"id": ref.id},
            ).scalar_one()
            == "model_request"
        )
    return ref


def _roundtrip(drill_databases, tmp_path, source, storage, target):
    from backend.app.operations_backup import (
        BackupLimits,
        PgTools,
        PostgresDatabase,
        create_backup,
        restore_backup,
        verify_backup,
    )

    tools = PgTools(
        pg_dump=(drill_databases.pg_dump,), pg_restore=(drill_databases.pg_restore,)
    )
    limits = BackupLimits()
    archive = tmp_path / "private-neutral-backup"
    source_db = PostgresDatabase(
        drill_databases.source.render_as_string(hide_password=False)
    )
    manifest = create_backup(
        database=source_db,
        storage_root=storage,
        destination=archive,
        durable_parent=tmp_path,
        application_ref=legacy.APPLICATION_REF,
        quiesced=True,
        tools=tools,
        limits=limits,
    )
    assert (
        verify_backup(archive, application_ref=legacy.APPLICATION_REF, limits=limits)
        == manifest
    )
    restore_backup(
        database=PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        ),
        backup_root=archive,
        storage_root=target,
        durable_parent=tmp_path,
        application_ref=legacy.APPLICATION_REF,
        quiesced=True,
        trust_backup=True,
        tools=tools,
        limits=limits,
    )
    return manifest


@pytest.mark.parametrize("revision", ["011_note_pin_trigger", "013_factor_import"])
def test_historical_upgrade_preserves_legacy_pins_and_allows_neutral_publication(
    drill_databases, tmp_path: Path, revision
):
    source, restored = (
        legacy._engine(drill_databases.source),
        legacy._engine(drill_databases.restored),
    )
    storage, target = tmp_path / "source-storage", tmp_path / "restored-storage"
    try:
        notes, package = factors._seed_legacy(source, storage)
        if revision == "013_factor_import":
            legacy._upgrade(source, revision)
            seeded = factors._seed_factors(source, storage)
            factors._import_code_only(source, storage, seeded)
        before = legacy._historical_snapshot(source)
        old_files = factors._files(storage)
        assert (
            before["research_note_evidence_pins"][1] and before["strategy_packages"][1]
        )
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == revision
        legacy._assert_historical_snapshot(restored, before)
        assert factors._files(target) == old_files
        legacy._upgrade(restored, HEAD)
        legacy._assert_historical_snapshot(restored, before)
        legacy._reopen_restored_notes(restored, target, notes)
        _publish_after_restore(restored, target)
        for key, data in old_files.items():
            assert (target / key).read_bytes() == data
        with restored.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT id FROM strategy_packages WHERE id=:id"),
                    {"id": package},
                ).scalar_one()
                == package
            )
    finally:
        source.dispose()
        restored.dispose()


@pytest.mark.parametrize(
    "backup_revision",
    [
        "015_neutral_primitives",
        "016_process_versioning",
        "017_numeric_process_boundary",
        "018_rd_source_persistence",
        "019_native_hdf_storage",
        "020_rd_controller",
        "021_execution_snapshots",
    ],
)
def test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting(
    drill_databases, tmp_path: Path, backup_revision
):
    from test_research_process_recovery import actual_peer

    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.research_broker_services import (
        abort_context,
        mark_submitted,
        open_context,
        open_operation,
        reserve_transport,
    )
    from backend.app.research_lifecycle import neutral_transaction
    from packages.research_runtime.broker_contracts import FixtureContext, digest
    from workers.research.model_broker import execute_fixture_once

    source, restored = (
        legacy._engine(drill_databases.source),
        legacy._engine(drill_databases.restored),
    )
    storage, target = tmp_path / "source-storage", tmp_path / "restored-storage"
    try:
        notes, _ = factors._seed_legacy(source, storage)
        legacy._upgrade(source, HEAD)
        settings = _settings(source, storage)
        factory = sessionmaker(source, expire_on_commit=False)
        fence = _new_fence(source, settings)
        _initialize(factory, fence)
        request, request_ref = _publish_request(factory, fence, settings)
        with neutral_transaction(factory, fence) as session:
            context = open_context(
                session, fence, FixtureContext(scenario="chat", ordinal=0)
            )
            operation = open_operation(
                session,
                fence,
                context.id,
                purpose="chat",
                ordinal=0,
                request=request,
                request_object=request_ref,
            )
            operation_id = operation.id
            context_id = context.id
        # Actual isolated unit authenticates through SO_PEERCRED, observed kernel
        # membership and its operation ticket. The broker commits pre-submission
        # accounting and publishes an actual bounded fixture response.
        with actual_peer(
            factory, fence, settings, _required_image(), operation_id
        ) as peer:
            response = execute_fixture_once(
                factory,
                fence,
                operation_id,
                digest(request),
                authorization=peer,
                settings=settings,
            )
            assert response is not None
            assert (
                execute_fixture_once(
                    factory,
                    fence,
                    operation_id,
                    digest(request),
                    authorization=peer,
                    settings=settings,
                )
                == response
            )
        with neutral_transaction(factory, fence) as session:
            interrupted = open_operation(
                session,
                fence,
                context_id,
                purpose="chat",
                ordinal=1,
                request=request,
                request_object=request_ref,
            )
            interrupted_id = interrupted.id
        # This intentionally interrupted submission has no fabricated outcome:
        # reservation stays conservative even after every actual unit is stopped.
        with neutral_transaction(factory, fence) as session:
            transport = reserve_transport(
                session, fence, interrupted_id, digest(request)
            )
            transport_id = transport.id
        with neutral_transaction(factory, fence) as session:
            mark_submitted(session, fence, transport_id)
        with neutral_transaction(factory, fence) as session:
            abort_context(session, fence, context_id, reason="uncertain_transport")
        _finish(source, fence)
        if backup_revision != HEAD:
            from alembic import command
            from alembic.config import Config

            config = Config(
                str(Path(__file__).resolve().parents[2] / "backend/alembic.ini")
            )
            config.set_main_option(
                "script_location",
                str(Path(__file__).resolve().parents[2] / "backend/alembic"),
            )
            with source.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, backup_revision)
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        closure = source_db.source_snapshot(BackupLimits())["research_closure"]
        assert {
            row["state"] for row in closure["tables"]["research_model_transports"]
        } == {"completed", "submitted"}
        assert len(closure["tables"]["research_model_transports"]) == 2
        assert any(
            row["outcome"] == "measured" and row["charge"]["calls"] == 1
            for row in closure["tables"]["research_budget_settlements"]
        )
        assert any(
            row["state"] == "uncertain"
            for row in closure["tables"]["research_budget_reservations"]
        )
        assert closure["tables"]["research_process_receipts"]
        assert (
            closure["tables"]["research_context_aborts"][0]["reason"]
            == "uncertain_transport"
        )
        before = legacy._historical_snapshot(source)
        before_files = factors._files(storage)
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == backup_revision
        legacy._assert_historical_snapshot(restored, before)
        assert factors._files(target) == before_files
        restored_db = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        )
        assert (
            restored_db.source_snapshot(BackupLimits())["research_closure"] == closure
        )
        legacy._upgrade(restored, HEAD)
        legacy._reopen_restored_notes(restored, target, notes)
        _publish_after_restore(restored, target)
        for key, data in before_files.items():
            assert (target / key).read_bytes() == data
    finally:
        source.dispose()
        restored.dispose()
