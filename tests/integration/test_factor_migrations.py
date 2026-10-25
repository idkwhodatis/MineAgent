"""Real PostgreSQL reversible-empty and evidence-preserving factor migrations.

Every downgrade uses deployed Alembic SQL in one actual transaction. Missing PG
is a skip, never SQLite/mocked evidence. No historical migration test is relaxed.
"""

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.factor_import_models import FactorImportAdmission, FactorImportEnvelope
from backend.app.factor_models import Factor, FactorObject
from backend.app.models import DataUsageExposure

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def migration(connection, target, *, upgrade=False):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    config.attributes["connection"] = connection
    (command.upgrade if upgrade else command.downgrade)(config, target)


def revision(engine):
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def object_row(owner_id):
    checksum = sha256(b"retained migration evidence").hexdigest()
    return FactorObject(
        id=uuid4(),
        owner_id=owner_id,
        storage_key=f"factors/{owner_id}/objects/{checksum}.zip",
        sha256=checksum,
        size_bytes=len(b"retained migration evidence"),
        media_type="application/zip",
        object_kind="package",
    )


def refusal(engine, target, match):
    before = revision(engine)
    tables = set(inspect(engine).get_table_names())
    with pytest.raises(DBAPIError, match=match), engine.begin() as connection:
        migration(connection, target)
    assert revision(engine) == before
    assert set(inspect(engine).get_table_names()) == tables


def test_empty_factor_import_migration_round_trip_preserves_core(
    migrated_engine, owner
):
    factor_id = uuid4()
    with Session(migrated_engine) as session:
        session.add(
            Factor(
                id=factor_id,
                owner_id=owner.user.id,
                name="Core evidence is not an import",
            )
        )
        session.commit()
    with migrated_engine.begin() as connection:
        before = (
            connection.execute(select(Factor.__table__).where(Factor.id == factor_id))
            .mappings()
            .one()
        )
        migration(connection, "012_factor_library")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "012_factor_library"
        )
        assert "factor_import_admissions" not in inspect(connection).get_table_names()
        assert (
            connection.execute(select(Factor.__table__).where(Factor.id == factor_id))
            .mappings()
            .one()
            == before
        )
        migration(connection, "head", upgrade=True)
        assert (
            connection.execute(select(Factor.__table__).where(Factor.id == factor_id))
            .mappings()
            .one()
            == before
        )
    assert revision(migrated_engine) == "013_factor_import"


def test_empty_factor_library_migration_round_trip_preserves_legacy(
    migrated_engine, owner
):
    original_tables = set(inspect(migrated_engine).get_table_names())
    with migrated_engine.begin() as connection:
        before = connection.execute(
            text("SELECT id,code,content_sha256 FROM strategy_versions WHERE id=:id"),
            {"id": owner.strategy.id},
        ).one()
        migration(connection, "011_note_pin_trigger")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "011_note_pin_trigger"
        )
        assert "factors" not in inspect(connection).get_table_names()
        assert (
            connection.execute(
                text(
                    "SELECT id,code,content_sha256 FROM strategy_versions WHERE id=:id"
                ),
                {"id": owner.strategy.id},
            ).one()
            == before
        )
        migration(connection, "head", upgrade=True)
        assert (
            connection.execute(
                text(
                    "SELECT id,code,content_sha256 FROM strategy_versions WHERE id=:id"
                ),
                {"id": owner.strategy.id},
            ).one()
            == before
        )
    assert set(inspect(migrated_engine).get_table_names()) == original_tables


def test_factor_import_migration_refuses_pending_admission(migrated_engine, owner):
    with Session(migrated_engine) as session:
        obj = object_row(owner.user.id)
        session.add(obj)
        session.flush()
        admission = FactorImportAdmission(
            id=uuid4(),
            owner_id=owner.user.id,
            archive_object_id=obj.id,
            archive_sha256=obj.sha256,
            idempotency_key=uuid4().hex,
            request_sha256="b" * 64,
            parser_image_id="sha256:" + "c" * 64,
            status="queued",
            generation=0,
        )
        session.add(admission)
        session.flush()
        admission_id = admission.id
        session.commit()
    refusal(
        migrated_engine,
        "012_factor_library",
        "Factor import evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(FactorImportAdmission, admission_id).status == "queued"


def test_factor_import_migration_refuses_retained_envelope(migrated_engine, owner):
    with Session(migrated_engine) as session:
        obj = object_row(owner.user.id)
        session.add(obj)
        session.flush()
        envelope = FactorImportEnvelope(
            id=uuid4(),
            owner_id=owner.user.id,
            archive_object_id=obj.id,
            archive_sha256=obj.sha256,
            origin_namespace="declared-migration-fixture",
            origin_payload={},
            origin_payload_sha256="a" * 64,
            local_bindings={},
            local_bindings_sha256="b" * 64,
            exposure_history={
                "completeness": "unknown",
                "unresolved_reasons": ["external_history"],
            },
        )
        session.add(envelope)
        session.flush()
        envelope_id = envelope.id
        session.commit()
    refusal(
        migrated_engine,
        "012_factor_library",
        "Factor import evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(FactorImportEnvelope, envelope_id) is not None


def test_factor_library_migration_refuses_orphan_object_registration(
    migrated_engine, owner
):
    with Session(migrated_engine) as session:
        obj = object_row(owner.user.id)
        object_id = obj.id
        session.add(obj)
        session.commit()
    refusal(
        migrated_engine,
        "011_note_pin_trigger",
        "Factor evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(FactorObject, object_id) is not None


def test_factor_library_migration_refuses_retained_factor(migrated_engine, owner):
    factor_id = uuid4()
    with Session(migrated_engine) as session:
        session.add(
            Factor(
                id=factor_id,
                owner_id=owner.user.id,
                name="Retain even without versions",
            )
        )
        session.commit()
    refusal(
        migrated_engine,
        "011_note_pin_trigger",
        "Factor evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(Factor, factor_id).name == "Retain even without versions"


def add_exposure(engine, owner, *, imported):
    with Session(engine) as session:
        row = DataUsageExposure(
            id=uuid4(),
            owner_id=owner.user.id,
            dataset_version_id=owner.dataset.id,
            source_key=("factor:import:" if imported else "factor:") + uuid4().hex,
            identities=[],
            start_date=owner.dataset.start_date,
            end_date=owner.dataset.end_date,
            purpose="imported_factor_research_exposure"
            if imported
            else "factor_values",
            provenance={"fixture": "retained external dependency"},
        )
        row_id = row.id
        session.add(row)
        session.commit()
    return row_id


def test_factor_library_migration_refuses_external_exposure_dependency(
    migrated_engine, owner
):
    row_id = add_exposure(migrated_engine, owner, imported=False)
    refusal(
        migrated_engine,
        "011_note_pin_trigger",
        "Factor evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(DataUsageExposure, row_id) is not None


def test_factor_import_migration_refuses_external_exposure_dependency(
    migrated_engine, owner
):
    row_id = add_exposure(migrated_engine, owner, imported=True)
    refusal(
        migrated_engine,
        "012_factor_library",
        "Factor import evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(DataUsageExposure, row_id) is not None


def test_factor_library_migration_refuses_concurrent_insert(migrated_engine, owner):
    factor_id = uuid4()
    before = revision(migrated_engine)
    # Deliberately bypass advisory admission: ACCESS EXCLUSIVE NOWAIT must also
    # catch an uncommitted raw writer, rather than check an invisible empty table.
    with migrated_engine.begin() as writer:
        writer.execute(
            insert(Factor).values(
                id=factor_id, owner_id=owner.user.id, name="Concurrent raw writer"
            )
        )
        with (
            pytest.raises(DBAPIError, match="could not obtain lock"),
            migrated_engine.begin() as connection,
        ):
            migration(connection, "011_note_pin_trigger")
    assert revision(migrated_engine) == before
    with Session(migrated_engine) as session:
        assert session.get(Factor, factor_id) is not None


@pytest.mark.parametrize("stage", ["library", "import"])
def test_factor_migration_refuses_shared_maintenance_admission(migrated_engine, stage):
    from backend.app.core.maintenance import _lock

    target = "011_note_pin_trigger" if stage == "library" else "012_factor_library"
    if stage == "library":
        with migrated_engine.begin() as connection:
            migration(connection, "012_factor_library")
    before = revision(migrated_engine)
    with migrated_engine.begin() as reader:
        _lock(reader)  # The same shared admission used by every managed session.
        with (
            pytest.raises(DBAPIError, match="quiescent maintenance admission"),
            migrated_engine.begin() as connection,
        ):
            migration(connection, target)
    assert revision(migrated_engine) == before
    # A denied attempt must release all its locks without poisoning later work.
    with migrated_engine.begin() as connection:
        migration(connection, target)
        migration(connection, "head", upgrade=True)


@pytest.mark.parametrize(
    "dependency",
    ["library-storage", "library-source", "library-profile", "import-source"],
)
def test_factor_migration_refuses_dataset_only_dependency(
    migrated_engine, owner, dependency
):
    from backend.app.models import DatasetVersion

    dataset_id = uuid4()
    source = "synthetic_fixture"
    storage_key = f"datasets/{dataset_id}"
    manifest = {"schema_version": 1}
    if dependency == "library-storage":
        storage_key = f"factors/{owner.user.id}/datasets/{dataset_id}"
    elif dependency == "library-source":
        source = "mineagent_fixed_factor_fixture"
    elif dependency == "library-profile":
        manifest = {"schema_version": "mineagent.factor-fixture-dataset.v1"}
    else:
        source = "imported_factor_synthetic"
    with Session(migrated_engine) as session:
        session.add(
            DatasetVersion(
                id=dataset_id,
                owner_id=owner.user.id,
                name="Retained dataset-only dependency",
                market="US",
                start_date=owner.dataset.start_date,
                end_date=owner.dataset.end_date,
                source=source,
                is_synthetic=True,
                content_sha256="a" * 64,
                manifest=manifest,
                storage_key=storage_key,
            )
        )
        session.commit()
    target = (
        "012_factor_library"
        if dependency == "import-source"
        else "011_note_pin_trigger"
    )
    match = (
        "Factor import evidence and retained dependencies"
        if dependency == "import-source"
        else "Factor evidence and retained dependencies"
    )
    refusal(migrated_engine, target, match)
    with Session(migrated_engine) as session:
        retained = session.get(DatasetVersion, dataset_id)
        assert retained.storage_key == storage_key and retained.manifest == manifest


@pytest.mark.parametrize(
    "dependency", ["version-provenance", "snapshot-provenance", "snapshot-verification"]
)
def test_factor_import_migration_refuses_provenance_only_dependency(
    migrated_engine, owner, settings, dependency
):
    from sqlalchemy import event

    from backend.app.factor_models import FactorVersion, FeatureSnapshot
    from backend.app.factor_schemas import FixedFixtureCreate
    from backend.app.factor_services import create_fixed_fixture

    settings.factor_library_enabled = True
    settings.data_provider_mode = "fixture"
    target = FactorVersion if dependency == "version-provenance" else FeatureSnapshot

    def retain_reference(mapper, connection, row):
        # Populate a legal INSERT with an orphan provenance reference. This does
        # not disable DB triggers or mutate append-only evidence after admission.
        if dependency == "version-provenance":
            row.source_provenance = {
                **row.source_provenance,
                "import_envelope_id": str(uuid4()),
            }
        elif dependency == "snapshot-provenance":
            row.source_claims = {
                **row.source_claims,
                "import_envelope_id": str(uuid4()),
            }
        else:
            row.local_verification = {
                **row.local_verification,
                "kind": "declared_or_unknown",
                "admission_profile": "isolated-import.v1",
            }

    event.listen(target, "before_insert", retain_reference)
    try:
        with Session(migrated_engine) as session:
            saved = create_fixed_fixture(
                session, owner.user.id, FixedFixtureCreate(), uuid4().hex, settings
            )
    finally:
        event.remove(target, "before_insert", retain_reference)
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM factor_import_envelopes")) == 0
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM factor_import_admissions"))
            == 0
        )
    refusal(
        migrated_engine,
        "012_factor_library",
        "Factor import evidence and retained dependencies",
    )
    with Session(migrated_engine) as session:
        assert session.get(FeatureSnapshot, saved["snapshot"]["id"]) is not None
        assert session.get(FactorVersion, saved["version"]["id"]) is not None


def test_factor_import_migration_refuses_concurrent_raw_admission_insert(
    migrated_engine, owner
):
    with Session(migrated_engine) as session:
        obj = object_row(owner.user.id)
        session.add(obj)
        session.flush()
        object_id, checksum = obj.id, obj.sha256
        session.commit()
    admission_id = uuid4()
    before = revision(migrated_engine)
    with migrated_engine.begin() as writer:
        writer.execute(
            insert(FactorImportAdmission).values(
                id=admission_id,
                owner_id=owner.user.id,
                archive_object_id=object_id,
                archive_sha256=checksum,
                idempotency_key=uuid4().hex,
                request_sha256="b" * 64,
                parser_image_id="sha256:" + "c" * 64,
                status="queued",
                generation=0,
            )
        )
        with (
            pytest.raises(DBAPIError, match="could not obtain lock"),
            migrated_engine.begin() as connection,
        ):
            migration(connection, "012_factor_library")
    assert revision(migrated_engine) == before
    with Session(migrated_engine) as session:
        assert session.get(FactorImportAdmission, admission_id).status == "queued"


@pytest.mark.parametrize("stage", ["library", "import"])
def test_factor_migration_rejects_stale_repeatable_read_snapshot(
    migrated_engine, owner, stage
):
    target = "011_note_pin_trigger" if stage == "library" else "012_factor_library"
    table = "factors" if stage == "library" else "factor_import_admissions"
    if stage == "library":
        with migrated_engine.begin() as connection:
            migration(connection, "012_factor_library")
    before = revision(migrated_engine)
    inserted_id = uuid4()
    with (
        migrated_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as stale,
        pytest.raises(DBAPIError, match="requires READ COMMITTED isolation"),
        stale.begin(),
    ):
        assert stale.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        with Session(migrated_engine) as writer:
            if stage == "library":
                writer.add(
                    Factor(
                        id=inserted_id,
                        owner_id=owner.user.id,
                        name="Committed after stale snapshot",
                    )
                )
            else:
                obj = object_row(owner.user.id)
                writer.add(obj)
                writer.flush()
                writer.add(
                    FactorImportAdmission(
                        id=inserted_id,
                        owner_id=owner.user.id,
                        archive_object_id=obj.id,
                        archive_sha256=obj.sha256,
                        idempotency_key=uuid4().hex,
                        request_sha256="b" * 64,
                        parser_image_id="sha256:" + "c" * 64,
                        status="queued",
                        generation=0,
                    )
                )
            writer.commit()
        # Prove the dangerous condition: the old transaction really cannot
        # see newly committed evidence, although its writer released locks.
        assert stale.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        migration(stale, target)
    assert revision(migrated_engine) == before
    with Session(migrated_engine) as session:
        assert (
            session.get(
                Factor if stage == "library" else FactorImportAdmission, inserted_id
            )
            is not None
        )
