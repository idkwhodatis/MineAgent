"""Composed cold validators on actual empty reader outputs; not PostgreSQL proof."""

import time

import pytest

from backend.app import (
    execution_snapshot_backup,
    factor_analysis_backup,
    factor_native_backup,
    openevolve_backup_shape,
    rdagent_backup,
    rdagent_submission_backup,
    research_backup,
    structured_factor_backup,
)
from backend.app.operations_backup import (
    BackupError,
    BackupLimits,
    _validate_references,
)
from backend.app.operations_retention import (
    RetentionError,
    _protected,
    _snapshot_manifest_keys,
)
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_native_wiring import snapshot


class EmptyConnection:
    """Only the database row source is empty; no closure validator is replaced."""

    def execution_options(self, **_kwargs):
        return self

    def execute(self, *_args, **_kwargs):
        return self

    def scalars(self):
        return self

    def __iter__(self):
        return iter(())

    def all(self):
        return []

    def close(self):
        pass


def composed_snapshot(revision):
    value = snapshot()
    value["schema_revision"] = revision
    cap = capabilities_for(revision)
    db = EmptyConnection()
    value["research_closure"] = research_backup.read_closure(db, 100, revision=revision)
    value["rd_source_closure"] = rdagent_backup.read_closure(db, 100, revision=revision)
    value["native_closure"] = factor_native_backup.read_closure(
        db, 100, schema=cap.native_closure_schema
    )
    value["execution_closure"] = execution_snapshot_backup.read_closure(
        db, 100, schema=cap.execution_snapshot_schema
    )
    value["direct_analysis_closure"] = factor_analysis_backup.read_closure(db, 100)
    if cap.rd_submission_schema:
        value["rd_submission_closure"] = rdagent_submission_backup.read_closure(db, 100)
    if cap.openevolve_closure_schema:
        value["openevolve_closure"] = openevolve_backup_shape.read_closure(db, 100)
    if cap.structured_strategy_schema:
        value["structured_strategy_closure"] = structured_factor_backup.read_closure(
            db, 100
        )
    if cap.rd_steer_schema:
        from backend.app.rdagent_steer_backup import read_closure

        value["rd_steer_closure"] = read_closure(db, 100)
    if cap.rd_seed_schema:
        from backend.app.rdagent_seed_backup import read_closure

        value["rd_seed_closure"] = read_closure(db, 100)
    return value


@pytest.mark.parametrize(
    "revision",
    [
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
    ],
)
def test_full_empty_cold_dispatch_preserves_exact_registered_tuples(tmp_path, revision):
    value = composed_snapshot(revision)
    _validate_references(tmp_path, [], value, BackupLimits())
    assert _snapshot_manifest_keys(value, BackupLimits()) == set()
    _, trees = _protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize(
    "field,schema",
    [
        ("research_closure", "mineagent.research-restore-closure.v10"),
        ("native_closure", factor_native_backup.SCHEMA_V2),
        ("rd_source_closure", rdagent_backup.SCHEMA_V3),
        ("openevolve_closure", None),
    ],
)
def test_full024_rejects_unknown_or_historical_mixed_tuple(tmp_path, field, schema):
    value = composed_snapshot("024_openevolve_initial")
    if schema is None:
        value.pop(field)
    else:
        value[field]["schema_version"] = schema
    with pytest.raises(BackupError):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _protected(value, {}, {}, time.monotonic() + 10)


@pytest.mark.parametrize(
    "revision",
    [
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
    ],
)
def test_actual_source_snapshot_envelope_survives_full_cold_validation(
    tmp_path, monkeypatch, revision
):
    """Exercise actual serialization, not a manually assembled reference envelope."""
    from backend.app.operations_backup import PostgresDatabase

    class Source(EmptyConnection):
        def execute(self, query, *_args, **_kwargs):
            self.query = str(query)
            return self

        def all(self):
            return (
                [revision]
                if "SELECT version_num FROM alembic_version" in self.query
                else []
            )

        def scalar_one(self):
            assert "count(*)" in self.query
            return 0

        def mappings(self):
            return self

    database = PostgresDatabase("postgresql://test@127.0.0.1/test")
    source = Source()
    monkeypatch.setattr(database, "_inspect", lambda inspect: inspect(source))
    monkeypatch.setattr(database, "_profile", lambda connection: 16)
    value = database.source_snapshot(BackupLimits())
    assert "structured_links_pending" not in value
    _validate_references(tmp_path, [], value, BackupLimits())
    assert _snapshot_manifest_keys(value, BackupLimits()) == set()
    _, trees = _protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize(
    "revision,field,schema",
    [
        (
            "027_rd_controller_v3",
            "research_closure",
            "mineagent.research-restore-closure.v10",
        ),
        (
            "027_rd_controller_v3",
            "rd_source_closure",
            "mineagent.rd-source-restore-closure.v5",
        ),
        (
            "026_rd_seeded_submissions",
            "research_closure",
            "mineagent.research-restore-closure.v11",
        ),
        (
            "026_rd_seeded_submissions",
            "rd_source_closure",
            "mineagent.rd-source-restore-closure.v6",
        ),
    ],
)
def test_controller_revision_cannot_mix_old_and_new_closure_authority(
    tmp_path, revision, field, schema
):
    value = composed_snapshot(revision)
    value[field]["schema_version"] = schema
    with pytest.raises(BackupError):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _protected(value, {}, {}, time.monotonic() + 10)
