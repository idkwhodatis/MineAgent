"""Real PostgreSQL factor cold restore, not parser or model execution evidence.

Uses two disposable databases and the established pg_dump/pg_restore harness.
RUN_BACKUP_RESTORE_DRILL=1 is fail-closed; collection/skips are not acceptance.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import test_backup_restore as legacy
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as shared_drill_databases

from tests.integration.historical_run_fixture import historical_builder

drill_databases = shared_drill_databases

pytestmark = pytest.mark.postgres


def _files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


@historical_builder
def _legacy_package(engine, storage):
    """Persistence-only retained export, not a successful evaluator assertion."""
    from backend.app.core.atomic_storage import publish_bytes
    from backend.app.models import (
        Candidate,
        ExecutionAttempt,
        Experiment,
        FinalSelection,
        ResearchCheckpoint,
        ResearchRun,
        SplitVersion,
        StrategyPackage,
        UniverseVersion,
    )
    from packages.factors.contracts import canonical_bytes, digest

    with Session(engine) as session:
        original = session.scalar(
            select(ResearchRun).where(
                ResearchRun.idempotency_key == "historical-cold-restore"
            )
        )
        experiment = Experiment(
            owner_id=original.owner_id, name="Synthetic archive persistence boundary"
        )
        session.add(experiment)
        session.flush()
        split = SplitVersion(
            owner_id=original.owner_id,
            experiment_id=experiment.id,
            dataset_version_id=original.dataset_version_id,
            universe_version_id=session.scalar(select(UniverseVersion.id)),
            definition={"synthetic_persistence_only": True},
            content_sha256=digest({"synthetic_persistence_only": True}),
        )
        session.add(split)
        session.flush()

        def clone(cls, original_row, **changes):
            values = {
                c.name: getattr(original_row, c.name) for c in cls.__table__.columns
            }
            values.update(id=uuid4(), **changes)
            row = cls(**values)
            session.add(row)
            session.flush()
            return row

        run = clone(
            ResearchRun,
            original,
            idempotency_key="factor-restore-package-boundary",
            experiment_id=experiment.id,
            split_version_id=split.id,
            active_attempt_id=None,
            latest_checkpoint_id=None,
        )
        attempt = clone(
            ExecutionAttempt,
            session.scalar(
                select(ExecutionAttempt).where(ExecutionAttempt.run_id == original.id)
            ),
            run_id=run.id,
        )
        candidate = clone(
            Candidate,
            session.scalar(
                select(Candidate).where(
                    Candidate.run_id == original.id, Candidate.parent_id.is_(None)
                )
            ),
            run_id=run.id,
            attempt_id=attempt.id,
        )
        checkpoint = clone(
            ResearchCheckpoint,
            session.scalar(
                select(ResearchCheckpoint).where(
                    ResearchCheckpoint.run_id == original.id
                )
            ),
            run_id=run.id,
            attempt_id=attempt.id,
        )
        selection = FinalSelection(
            owner_id=run.owner_id,
            experiment_id=experiment.id,
            split_version_id=split.id,
            source_run_id=run.id,
            source_candidate_id=candidate.id,
            source_checkpoint_id=checkpoint.id,
            strategy_version_id=run.strategy_version_id,
            request_sha256=digest({"persistence_only": str(run.id)}),
            source_config_sha256=checkpoint.config_sha256,
            source_code_sha256=candidate.code_sha256,
            source_checkpoint_sha256=checkpoint.manifest_sha256,
            selection_evidence={"synthetic_persistence_only": True},
            exposure_snapshot={"history": "unknown"},
        )
        session.add(selection)
        session.flush()
        content = canonical_bytes(
            {
                "synthetic_persistence_only": True,
                "selection_id": str(selection.id),
                "note": "历史归档，不代表执行结果",
            }
        )
        sha = legacy._sha(content)
        key = f"strategy-packages/{run.owner_id}/{uuid4()}.json"
        publish_bytes(storage / key, content, sha256=sha, durable_root=storage)
        package = StrategyPackage(
            owner_id=run.owner_id,
            final_selection_id=selection.id,
            request_sha256=digest({"selection": str(selection.id)}),
            request={"synthetic_persistence_only": True},
            content_sha256=sha,
            archive_sha256=sha,
            size_bytes=len(content),
            storage_key=key,
            manifest={"synthetic_persistence_only": True},
            exposure_snapshot={"history": "unknown"},
            exposure_as_of=original.created_at,
        )
        session.add(package)
        session.commit()
        return package.id


def _seed_legacy(engine, storage):
    legacy._upgrade(engine, "003_m3")
    legacy._seed_m3(engine, storage)
    legacy._upgrade(engine, "011_note_pin_trigger")
    legacy._seed_workbench_evidence(engine)
    legacy._seed_comparison_boundary(engine, storage)
    legacy._seed_independent_boundary(engine)
    notes = legacy._seed_note_boundary(engine, storage)
    package_id = _legacy_package(engine, storage)
    return notes, package_id


def _factor_settings(engine, storage):
    return legacy._note_settings(engine, storage).model_copy(
        update={"factor_library_enabled": True, "data_provider_mode": "fixture"}
    )


def _seed_factors(engine, storage):
    from backend.app.core.atomic_storage import provision_directory
    from backend.app.factor_schemas import (
        FactorBundleCreate,
        FactorCreate,
        FactorExportCreate,
        FixedFixtureCreate,
    )
    from backend.app.factor_services import (
        create_bundle,
        create_factor,
        create_fixed_fixture,
        export_package,
        snapshot_values,
    )
    from packages.factors.contracts import Parameter, source_sha256
    from packages.factors.fixtures import fixture_definition

    provision_directory(storage / "factors", durable_parent=storage)
    settings = _factor_settings(engine, storage)
    with Session(engine) as session:
        owner = session.scalar(text("SELECT id FROM users LIMIT 1"))
        fixture = create_fixed_fixture(
            session,
            owner,
            FixedFixtureCreate(name="真实固定动量样例"),
            "restore-fixture",
            settings,
        )
        source = "# 手工未执行因子\nVALUE = 1\n"
        definition = fixture_definition("momentum").model_copy(
            update={
                "code_sha256": source_sha256(source),
                "parameters": (
                    Parameter(name="说明", value="中文定义用于验证不可变哈希"),
                ),
            }
        )
        manual = create_factor(
            session,
            owner,
            FactorCreate(name="中文定义", code=source, definition=definition),
            "restore-manual",
            settings,
        )
        bundle = create_bundle(
            session,
            owner,
            FactorBundleCreate(
                name="中文组合",
                members=[
                    {
                        "factor_version_id": fixture["version"]["id"],
                        "definition_sha256": fixture["version"]["definition_sha256"],
                    },
                    {
                        "factor_version_id": manual["version"]["id"],
                        "definition_sha256": manual["version"]["definition_sha256"],
                    },
                ],
            ),
            "restore-bundle",
            settings,
        )
        snapshot_id = fixture["snapshot"]["id"]
        page = snapshot_values(session, owner, snapshot_id, settings, limit=100)
        package = export_package(
            session,
            owner,
            fixture["version"]["id"],
            FactorExportCreate(
                snapshot_ids=[snapshot_id],
                evaluation_ids=[fixture["evaluation"]["id"]],
                include_values=True,
            ),
            "restore-export",
            settings,
        )
        code_package = export_package(
            session,
            owner,
            fixture["version"]["id"],
            FactorExportCreate(),
            "restore-code-export",
            settings,
        )
        assert page["count"] == 24
        assert fixture["snapshot"]["local_verification"]["source_pit_verified"] is False
        return {
            "owner": owner,
            "fixture": fixture,
            "manual": manual,
            "bundle": bundle,
            "package": package,
            "code_package": code_package,
            "page": page,
        }


def _wrong_archive_rejected(engine, seeded):
    """Direct SQL mismatch must fail even for the same owner; no parser claims."""
    from backend.app.factor_import_models import (
        FactorImportAdmission,
        FactorImportEnvelope,
    )
    from packages.factors.contracts import digest

    left, right = seeded["package"], seeded["code_package"]
    assert left["archive_sha256"] != right["archive_sha256"]
    # The whole fixture transaction rolls back, including the synthetic envelope.
    with Session(engine) as session:
        envelope = FactorImportEnvelope(
            owner_id=seeded["owner"],
            archive_object_id=right["archive_object_id"],
            archive_sha256=right["archive_sha256"],
            origin_namespace="test-persistence-only",
            origin_payload=right["definition"],
            origin_payload_sha256=digest(right["definition"]),
            local_bindings={},
            local_bindings_sha256=digest({}),
            exposure_history={"completeness": "unknown"},
        )
        session.add(envelope)
        session.flush()
        values = dict(  # noqa: C408 -- named SQL bindings
            id=uuid4(),
            owner_id=seeded["owner"],
            archive_object_id=left["archive_object_id"],
            archive_sha256=left["archive_sha256"],
            idempotency_key="wrong-archive",
            request_sha256="a" * 64,
            parser_image_id="sha256:" + "b" * 64,
            status="succeeded",
            generation=0,
            result_envelope_id=envelope.id,
        )
        with pytest.raises(DBAPIError) as error:
            session.execute(FactorImportAdmission.__table__.insert().values(**values))
            session.flush()
        assert getattr(error.value.orig, "sqlstate", None) == "23503"
        session.rollback()


def _import_code_only(engine, storage, seeded):
    """Actual admitted worker state machine; no tabular parser is invoked."""
    from sqlalchemy.orm import sessionmaker

    from backend.app.factor_import_models import (
        FactorImportAdmission,
        FactorImportEnvelope,
    )
    from backend.app.factor_import_services import admit_import
    from backend.app.factor_models import FactorObject
    from backend.app.storage import read_verified
    from packages.factors.package import decode_package
    from workers.research.factor_import import process_one

    class NoTabularParser:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise AssertionError("Code-only package must not invoke native parser")

    settings = _factor_settings(engine, storage).model_copy(
        update={
            "factor_import_enabled": True,
            "factor_parser_image_id": "sha256:" + "b" * 64,
        }
    )
    with Session(engine) as session:
        package = seeded["code_package"]
        obj = session.get(FactorObject, package["archive_object_id"])
        archive_key, archive_sha = obj.storage_key, obj.sha256
        raw = read_verified(storage, archive_key, archive_sha, 10_000_000)
        admitted = admit_import(
            session, seeded["owner"], raw, "restore-code-import", settings
        )
    assert process_one(
        settings,
        admitted["id"],
        factory=sessionmaker(engine),
        parser_factory=NoTabularParser,
    )
    with Session(engine) as session:
        row = session.get(FactorImportAdmission, admitted["id"])
        assert row.status == "succeeded", row.error_code
        envelope = session.get(FactorImportEnvelope, row.result_envelope_id)
        assert envelope.origin_payload == decode_package(raw).manifest
        assert envelope.archive_sha256 == package["archive_sha256"]
        assert envelope.local_bindings["source_version_id"] == str(
            seeded["fixture"]["version"]["id"]
        )
        assert (
            envelope.local_bindings["version_id"]
            != envelope.local_bindings["source_version_id"]
        )
        assert envelope.local_bindings["snapshot_ids"] == []
        assert envelope.local_bindings["origin_map"]
        assert envelope.exposure_history
        from datetime import date

        from backend.app.experiment_services import (
            classify_exposures,
            has_unknown_import_history,
        )
        from packages.factors.contracts import DateWindow

        assert has_unknown_import_history(session, seeded["owner"])
        assert not has_unknown_import_history(session, uuid4())
        unknown = classify_exposures(
            [{"listing_id": "unrelated:known-listing", "symbol": "UNRELATED"}],
            DateWindow(start=date(2030, 1, 1), end=date(2030, 2, 1)),
            [],
            unknown_import_history=has_unknown_import_history(session, seeded["owner"]),
        )
        assert unknown["classification"] == "unknown_import_history"
        return envelope.id


def _actual_value_import(engine, storage, seeded):
    """Required hosted boundary: actual isolated Docker parser, never a test double."""
    from sqlalchemy.orm import sessionmaker

    from backend.app.factor_import_models import (
        FactorImportAdmission,
        FactorImportEnvelope,
    )
    from backend.app.factor_import_services import admit_import
    from backend.app.factor_models import FactorObject, FeatureSnapshot
    from backend.app.factor_services import snapshot_values
    from backend.app.storage import read_verified
    from packages.factors.contracts import FeatureRow, rows_digest
    from packages.factors.package import decode_package
    from runtimes.factor_parser.supervisor import assert_quiescent, factor_fence
    from workers.research.factor_import import process_one

    if os.environ.get("MINEAGENT_FACTOR_RESTORE_PARSER_REQUIRED") != "1":
        pytest.fail(
            "Import-aware factor restore requires MINEAGENT_FACTOR_RESTORE_PARSER_REQUIRED=1; actual parser was not run",
            pytrace=False,
        )
    image = os.environ.get("MINEAGENT_FACTOR_PARSER_IMAGE", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        pytest.fail(
            "Import-aware factor restore requires the actual built MINEAGENT_FACTOR_PARSER_IMAGE ID",
            pytrace=False,
        )
    settings = _factor_settings(engine, storage).model_copy(
        update={
            "factor_import_enabled": True,
            "factor_parser_image_id": image,
        }
    )
    with Session(engine) as session:
        package = seeded["package"]
        obj = session.get(FactorObject, package["archive_object_id"])
        archive_key, archive_sha = obj.storage_key, obj.sha256
        raw = read_verified(storage, archive_key, archive_sha, 10_000_000)
        admitted = admit_import(
            session, seeded["owner"], raw, "restore-real-value-import", settings
        )
    # No parser_factory argument: production DockerParser is mandatory here.
    assert process_one(settings, admitted["id"], factory=sessionmaker(engine))
    with Session(engine) as session:
        row = session.get(FactorImportAdmission, admitted["id"])
        assert row.status == "succeeded", row.error_code
        envelope = session.get(FactorImportEnvelope, row.result_envelope_id)
        assert envelope.origin_payload == decode_package(raw).manifest
        assert envelope.archive_sha256 == package["archive_sha256"]
        assert len(envelope.local_bindings["snapshot_ids"]) == 1
        snapshot_id = UUID(envelope.local_bindings["snapshot_ids"][0])
        snapshot = session.get(FeatureSnapshot, snapshot_id)
        assert snapshot.id != seeded["fixture"]["snapshot"]["id"]
        assert snapshot.factor_version_id != seeded["fixture"]["version"]["id"]
        assert (
            snapshot.manifest_sha256 != seeded["fixture"]["snapshot"]["manifest_sha256"]
        )
        assert snapshot.local_verification["kind"] == "declared_or_unknown"
        assert snapshot.local_verification["source_pit_verified"] is False
        assert (
            snapshot.source_claims["source_manifest_sha256"]
            == seeded["fixture"]["snapshot"]["manifest_sha256"]
        )
        assert envelope.local_bindings["exposure_admissions"]["admitted"]
        page = snapshot_values(
            session, seeded["owner"], snapshot_id, settings, limit=100
        )
        assert (
            rows_digest(tuple(FeatureRow.model_validate(r) for r in page["data"]))
            == snapshot.output_digest
        )
        original_values = [
            {k: v for k, v in r.items() if k != "factor_version_id"}
            for r in seeded["page"]["data"]
        ]
        derived_values = [
            {k: v for k, v in r.items() if k != "factor_version_id"}
            for r in page["data"]
        ]
        assert derived_values == original_values
        assert page["count"] == 24
        # Archive and origin metadata remain unchanged after actual derivation.
        assert read_verified(storage, archive_key, archive_sha, 10_000_000) == raw
    with factor_fence(storage):
        assert_quiescent(storage)
    return {"snapshot_id": snapshot_id, "page": page}


@pytest.mark.parametrize(
    "revision", ["011_note_pin_trigger", "013_factor_import", "014_run_identity"]
)
def test_real_factor_cold_restore_and_legacy_upgrade(
    drill_databases, tmp_path: Path, revision
):
    from backend.app.factor_backup import validate_closure
    from backend.app.factor_services import snapshot_values
    from backend.app.operations_backup import (
        BackupError,
        BackupLimits,
        PgTools,
        PostgresDatabase,
        create_backup,
        restore_backup,
        verify_backup,
    )

    source, restored = (
        legacy._engine(drill_databases.source),
        legacy._engine(drill_databases.restored),
    )
    storage, target = tmp_path / "source-storage", tmp_path / "restored-storage"
    archive = tmp_path / "private-factor-backup"
    tools = PgTools(
        pg_dump=(drill_databases.pg_dump,), pg_restore=(drill_databases.pg_restore,)
    )
    limits = BackupLimits()
    try:
        notes, legacy_package = _seed_legacy(source, storage)
        seeded = None
        derived = None
        if revision in {"013_factor_import", "014_run_identity"}:
            legacy._upgrade(source, revision)
            seeded = _seed_factors(source, storage)
            _wrong_archive_rejected(source, seeded)
            _import_code_only(source, storage, seeded)
            derived = _actual_value_import(source, storage, seeded)
        expected = legacy._historical_snapshot(source)
        expected_files = _files(storage)
        assert expected["research_note_evidence_pins"][1]
        assert expected["strategy_packages"][1]
        source_db = PostgresDatabase(
            drill_databases.source.render_as_string(hide_password=False)
        )
        snapshot = source_db.source_snapshot(limits)
        references = {item["path"] for item in snapshot["references"]}
        assert any(key.startswith("strategy-packages/") for key in references)
        assert any(key.startswith("runs/") for key in references)
        assert ("factor_closure" in snapshot) == (seeded is not None)
        if seeded:
            closure = snapshot["factor_closure"]
            assert closure["schema_version"] == "mineagent.factor-restore-closure.v2"
            assert closure["external_bindings"]["exposures"]
            for mutation in ("future", "missing_table", "extra_table"):
                invalid = copy.deepcopy(closure)
                if mutation == "future":
                    invalid["schema_version"] = "mineagent.factor-restore-closure.v999"
                elif mutation == "missing_table":
                    del invalid["tables"]["factor_import_envelopes"]
                else:
                    invalid["tables"]["unexpected_table"] = []
                with pytest.raises(ValueError):
                    validate_closure(
                        invalid, lambda *args: None, lambda *args: {}, limits.max_files
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
        assert manifest["schema_revision"] == revision
        # A valid completion digest cannot authorize future schema capabilities.
        from packages.factors.contracts import canonical_bytes

        future = tmp_path / "unsupported-future-backup"
        future.mkdir()
        invalid_manifest = {**manifest, "schema_revision": "999_future"}
        invalid_bytes = canonical_bytes(invalid_manifest)
        (future / "manifest.json").write_bytes(invalid_bytes)
        (future / "COMPLETE").write_bytes(legacy._sha(invalid_bytes).encode() + b"\n")
        with pytest.raises(BackupError, match="Unsupported backup manifest"):
            verify_backup(future, application_ref=legacy.APPLICATION_REF, limits=limits)
        assert (
            verify_backup(
                archive, application_ref=legacy.APPLICATION_REF, limits=limits
            )
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
        legacy._assert_historical_snapshot(restored, expected)
        assert _files(target) == expected_files
        # Verify real archived old rows and bytes before upgrading, then read
        # them through current services only at the executable's required head.
        legacy._upgrade(restored, "015_neutral_primitives")
        legacy._assert_historical_snapshot(restored, expected)
        legacy._reopen_restored_notes(restored, target, notes)
        if not seeded:
            seeded = _seed_factors(restored, target)
            _wrong_archive_rejected(restored, seeded)
            _import_code_only(restored, target, seeded)
        with Session(restored) as session:
            page = snapshot_values(
                session,
                seeded["owner"],
                seeded["fixture"]["snapshot"]["id"],
                _factor_settings(restored, target),
                limit=100,
            )
            assert page == seeded["page"]
            from backend.app.experiment_services import has_unknown_import_history

            assert has_unknown_import_history(session, seeded["owner"])
            if derived:
                restored_derived = snapshot_values(
                    session,
                    seeded["owner"],
                    derived["snapshot_id"],
                    _factor_settings(restored, target),
                    limit=100,
                )
                assert restored_derived == derived["page"]
        # Restored journals prove prior parser shutdown without binding the new
        # filesystem inode to a stale live process, so future publication works.
        if seeded:
            from backend.app.factor_schemas import FactorBundleCreate
            from backend.app.factor_services import create_bundle
            from runtimes.factor_parser.supervisor import assert_quiescent, factor_fence

            with factor_fence(target):
                assert_quiescent(target)
            with Session(restored) as session:
                create_bundle(
                    session,
                    seeded["owner"],
                    FactorBundleCreate(
                        name="恢复后可继续发布",
                        members=[
                            {
                                "factor_version_id": seeded["fixture"]["version"]["id"],
                                "definition_sha256": seeded["fixture"]["version"][
                                    "definition_sha256"
                                ],
                            }
                        ],
                    ),
                    "restored-publication",
                    _factor_settings(restored, target),
                )
        with restored.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "015_neutral_primitives"
            )
            assert (
                connection.execute(
                    text("SELECT id FROM strategy_packages WHERE id=:id"),
                    {"id": legacy_package},
                ).scalar_one()
                == legacy_package
            )
        # Published factor bytes and complete declared identities remain immutable.
        for statement in (
            "UPDATE factor_versions SET code='changed'",
            "DELETE FROM feature_snapshot_objects",
            "DELETE FROM factor_evaluations",
        ):
            with pytest.raises(DBAPIError), restored.begin() as connection:
                connection.execute(text(statement))
        legacy._assert_historical_snapshot(source, expected)
    finally:
        source.dispose()
        restored.dispose()
