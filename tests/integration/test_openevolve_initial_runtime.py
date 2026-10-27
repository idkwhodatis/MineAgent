"""Required actual024 initial epoch and cold preservation; collection is not proof.

No SQLite, fake controller ACK, populated-result fixture, provider or skip path.
The source/model schedule is maintained input; all native observations, immutable
checkpoints and positive stops must be produced by the real protected runtimes.
"""

import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa:PLC0414

from tests.integration.openevolve_initial_fixture import (
    REVISION,
    assert_completed_epoch,
    prepare_initial_search,
    required_images,
    runtime_commitments,
)

pytestmark = pytest.mark.postgres


def _owner(engine):
    from backend.app.core.security import get_password_hash
    from backend.app.models import User

    identity = uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=identity,
                email=f"actual-oe-{identity}@example.test",
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            )
        )
        session.commit()
    return identity


def _proof(name, value):
    destination = os.environ.get("MINEAGENT_OE_INITIAL_PROOF")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True) + "\n"
        )


def test_actual_initial_epoch_four_saves_and_cold_restore(drill_databases, tmp_path):
    import test_backup_restore as legacy
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from workers.openevolve.operational import run_initial_epoch

    controller, runtime = required_images()
    source = legacy._engine(drill_databases.source)
    restored = legacy._engine(drill_databases.restored)
    storage, target = tmp_path / "oe-source-storage", tmp_path / "oe-restored-storage"
    try:
        legacy._upgrade(source, REVISION)
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage).model_copy(
            update={"lease_seconds": 1200}
        )
        fixture = prepare_initial_search(
            source,
            settings,
            _owner(source),
            controller_image=controller,
            runtime=runtime,
            profile_commitments=runtime_commitments(),
        )
        result = run_initial_epoch(
            fixture.factory,
            fixture.fence,
            runtime=runtime,
            schedule=fixture.schedule,
            settings=settings,
        )
        assert result["positive_stop"] is True and result["actual_exit_code"] == 124
        report = assert_completed_epoch(fixture.factory, fixture.fence, settings)
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        before = source_db.source_snapshot(BackupLimits())
        assert before["schema_revision"] == REVISION
        files = _files(storage)
        manifest = _roundtrip(drill_databases, tmp_path, source, storage, target)
        assert manifest["schema_revision"] == REVISION
        assert storage.stat().st_ino != target.stat().st_ino
        assert not (target / ".restore-incomplete").exists()
        assert _files(target) == files
        after = PostgresDatabase(
            drill_databases.restored.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        # All versioned family closures, not merely the latest selected candidate.
        closure_names = {key for key in before if key.endswith("_closure")}
        assert {"research_closure", "openevolve_closure"} <= closure_names
        assert closure_names == {key for key in after if key.endswith("_closure")}
        for key in closure_names:
            assert after[key] == before[key]
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        restored_settings = legacy._note_settings(restored, target)
        assert (
            assert_completed_epoch(restored_factory, fixture.fence, restored_settings)
            == report
        )
        # This historical comparison is data-only. No resumed controller/lease or
        # relocated kernel identity is minted from cold files.
        report.pop("run_id")
        _proof(
            "initial-cold",
            {
                **report,
                "schema_revision": REVISION,
                "actual_exit_code": 124,
                "cold_all_closures_equal": True,
                "cold_all_files_equal": True,
                "production_entrypoint": False,
            },
        )
    finally:
        source.dispose()
        restored.dispose()


def _run_owner(fixture, settings):
    from workers.openevolve.operational import run_initial_epoch

    # The inherited engine has no checked-out connection; child owns its pool.
    fixture.factory.kw["bind"].dispose(close=False)
    run_initial_epoch(
        fixture.factory,
        fixture.fence,
        runtime=fixture.runtime,
        schedule=fixture.schedule,
        settings=settings,
    )


def test_actual_initial_owner_loss_unread_original_deadline(migrated_engine, settings):
    import multiprocessing
    import time

    from sqlalchemy import select

    from backend.app.models import ResearchRun
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
    )
    from packages.research_runtime.process_persistence_codec import (
        ROW_FIELDS,
        decode_row,
    )
    from runtimes.openevolve_operational.process_journal import read_controller_journal
    from runtimes.openevolve_operational.supervisor import (
        BASE_IMAGE,
        _kernel_gone,
        verify_container,
        verify_image_inventory,
    )
    from runtimes.research_supervisor.supervisor import DockerSupervisor, operation_path

    controller, runtime = required_images()
    settings = settings.model_copy(update={"lease_seconds": 1200})
    fixture = prepare_initial_search(
        migrated_engine,
        settings,
        _owner(migrated_engine),
        controller_image=controller,
        runtime=runtime,
        profile_commitments=runtime_commitments(),
    )
    context = multiprocessing.get_context("fork")
    owner = context.Process(target=_run_owner, args=(fixture, settings))
    admission = launch = identity = child = None
    cli = DockerSupervisor(controller)
    base = json.loads(cli._command(["image", "inspect", BASE_IMAGE])[1])[0]
    image = json.loads(cli._command(["image", "inspect", controller])[1])[0]
    inventory = verify_image_inventory(base, image)
    owner.start()
    try:
        discovery = time.monotonic() + 90
        while admission is None:
            with fixture.factory() as session:
                row = session.scalar(
                    select(ResearchProcessAdmission).filter_by(
                        run_id=fixture.fence.run_id, role="openevolve_orchestrator"
                    )
                )
                if row is not None:
                    child_receipt = session.scalar(
                        select(ResearchProcessReceipt).filter_by(
                            admission_id=row.id, kind="controller_child"
                        )
                    )
                    if child_receipt is not None:
                        admission = decode_row(
                            {field: getattr(row, field) for field in ROW_FIELDS}
                        )
            assert owner.is_alive(), (
                "Actual owner exited before trusted controller identity"
            )
            assert time.monotonic() < discovery, "Controller child not observed"
            if admission is None:
                time.sleep(0.05)
        from backend.app.core.maintenance import shared_lifecycle

        with shared_lifecycle(migrated_engine, settings.storage_root):
            launch, identity, child, _stop = read_controller_journal(
                settings.storage_root, admission, with_child=True
            )
        assert not _kernel_gone(identity.leader) and not _kernel_gone(child.controller)
        # Kill only our exact host Process, never a runtime PID or a label set.
        owner.kill()
        owner.join(timeout=5)
        assert owner.exitcode == -9
        with fixture.factory() as session:
            held = session.get(ResearchBudgetReservation, admission.reservation_id)
            assert held.state == "reserved"
            before = (held.state, dict(held.amounts))
        # No reader, model fallback, signal, replacement issuer or deadline reset.
        absolute = launch.intent.not_after.timestamp()
        while not _kernel_gone(identity.leader):
            assert time.time() <= absolute + 1, "Autonomous original deadline exceeded"
            time.sleep(0.01)
        kernel_gone_at = time.time()
        assert absolute <= kernel_gone_at <= absolute + 1
        assert _kernel_gone(child.controller)
        document = cli._inspect(identity.container_id)
        while document["State"]["Running"]:
            assert time.time() <= absolute + 1, "Docker stop exceeded original bound"
            time.sleep(0.01)
            document = cli._inspect(identity.container_id)
        assert time.time() <= absolute + 1
        verify_container(
            document,
            launch,
            operation_path(settings.storage_root, admission),
            inventory,
            container_id=identity.container_id,
        )
        assert document["State"]["Running"] is False
        assert document["State"]["Status"] == "exited"
        assert (
            document["State"]["ExitCode"] == 124
            and document["State"]["OOMKilled"] is False
        )
        with fixture.factory() as session:
            held = session.get(ResearchBudgetReservation, admission.reservation_id)
            assert (held.state, dict(held.amounts)) == before
            assert session.get(ResearchRun, fixture.fence.run_id).status != "succeeded"
            assert (
                session.scalar(
                    select(ResearchProcessReceipt).filter_by(
                        admission_id=admission.id, kind="stop"
                    )
                )
                is None
            )
        from backend.app.core.maintenance import shared_lifecycle
        from backend.app.openevolve_controller_process import record_controller_stopped
        from backend.app.research_lifecycle import neutral_transaction
        from runtimes.openevolve_operational.recovery import recover_controller
        from runtimes.openevolve_operational.supervisor import ControllerSupervisor

        supervisor = ControllerSupervisor(controller, inventory)
        with shared_lifecycle(migrated_engine, settings.storage_root):
            stop = recover_controller(supervisor, settings.storage_root, admission)
            assert stop.confirmed_absent and stop.kernel_identity_absent
            assert (
                recover_controller(supervisor, settings.storage_root, admission) == stop
            )
            # Original1200s test lease is still current, not a forged renewal.
            # Record genuine containment only; no result import or budget refund.
            with neutral_transaction(
                fixture.factory, fixture.fence, resource_scope="processes"
            ) as session:
                record_controller_stopped(
                    session,
                    fixture.fence,
                    admission.id,
                    storage_root=settings.storage_root,
                )
            with fixture.factory() as session:
                held = session.get(ResearchBudgetReservation, admission.reservation_id)
                assert (held.state, dict(held.amounts)) == before
                assert (
                    session.get(ResearchRun, fixture.fence.run_id).status != "succeeded"
                )
                receipts = session.scalars(
                    select(ResearchProcessReceipt).filter_by(
                        admission_id=admission.id, kind="stop"
                    )
                ).all()
                assert len(receipts) == 1
        _proof(
            "owner-loss",
            {
                "original_deadline_seconds": 600,
                "actual_exit_code": 124,
                "kernel_absent_before_cleanup": True,
                "no_replacement_reader": True,
                "reservation_preserved": True,
                "successful_run": False,
                "persistent_recovery_proved": True,
            },
        )
    finally:
        if owner.is_alive():
            owner.kill()
            owner.join(timeout=5)
        # Cleanup only the exact retained stopped CID; no synthetic durable stop.
        # If containment is not established, leave the obligation visible and fail.
        if identity is not None:
            deadline = launch.intent.not_after.timestamp() + 1
            while not _kernel_gone(identity.leader) and time.time() <= deadline:
                time.sleep(0.02)
            if _kernel_gone(identity.leader):
                document = cli._inspect(identity.container_id)
                if document is not None:
                    assert (
                        document["Id"] == identity.container_id
                        and document["Image"] == controller
                    )
                    assert document["State"]["Running"] is False
                    cli._command(["rm", identity.container_id])
                assert cli._inspect(identity.container_id) is None

        if identity is not None and _kernel_gone(identity.leader):
            from backend.app.core.maintenance import shared_lifecycle
            from runtimes.research_supervisor.openevolve_native_supervisor import (
                NativeSupervisor,
            )

            with fixture.factory() as session:
                native_rows = session.scalars(
                    select(ResearchProcessAdmission).filter_by(
                        run_id=fixture.fence.run_id, role="native_evaluator"
                    )
                ).all()
                native_units = [
                    decode_row({field: getattr(row, field) for field in ROW_FIELDS})
                    for row in native_rows
                ]
            with shared_lifecycle(migrated_engine, settings.storage_root):
                for unit in native_units:
                    # Any native unit was launched before the host was killed;
                    # its own60s deadline precedes the controller's600s boundary.
                    NativeSupervisor(
                        runtime.protected_image_id, runtime.native_parent_image_id
                    ).stop_and_verify(settings.storage_root, unit)
