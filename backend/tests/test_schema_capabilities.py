"""Closed historical capabilities preserve import, note and package obligations."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import get_args

import pytest

from backend.app import factor_backup, operations_backup, operations_retention
from backend.app.operations_schemas import BackupReceipt
from backend.app.schema_capabilities import (
    BACKUP_REVISIONS,
    SCHEMA_CAPABILITIES,
    capabilities_for,
)
from backend.tests.test_backup_v1 import InspectionConnection


def test_capability_registry_and_values_are_immutable():
    with pytest.raises(TypeError):
        SCHEMA_CAPABILITIES["future"] = capabilities_for("014_run_identity")
    with pytest.raises(FrozenInstanceError):
        capabilities_for("014_run_identity").factor_imports = False


def test_receipt_and_backup_accept_exactly_the_explicit_capabilities():
    assert (
        set(get_args(BackupReceipt.model_fields["schema_revision"].annotation))
        == BACKUP_REVISIONS
    )
    assert operations_backup.REVISIONS == BACKUP_REVISIONS
    assert not {"001_m1", "002_m2"} & BACKUP_REVISIONS
    assert capabilities_for("014_run_identity") == capabilities_for("013_factor_import")
    assert capabilities_for("014_run_identity").strategy_packages
    assert capabilities_for("014_run_identity").note_pins
    assert capabilities_for("014_run_identity").factor_imports


@pytest.mark.parametrize(
    "revision", [None, 14, [], {}, "014", "015_future", "999_future"]
)
def test_unknown_capability_never_degrades_to_a_legacy_profile(revision):
    with pytest.raises(ValueError):
        capabilities_for(revision)


@pytest.mark.parametrize(
    "revision", ["001_m1", "011_note_pin_trigger", "015_future", None]
)
def test_factor_read_closure_refuses_nonfactor_and_unknown_revisions_before_sql(
    revision,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Nonfactor revision attempted factor-table SQL")

    with pytest.raises(ValueError):
        factor_backup.read_closure(
            SimpleNamespace(execute=forbidden), 100, revision=revision
        )


@pytest.mark.parametrize("revision", ["013_factor_import", "014_run_identity"])
def test_uncontained_import_blocks_backup_before_inventory(monkeypatch, revision):
    connection = InspectionConnection(
        revisions=[revision], blocking="FROM factor_import_admissions WHERE"
    )
    database = operations_backup.PostgresDatabase(
        "postgresql://operator@localhost/unit_inspection"
    )
    monkeypatch.setattr(database, "_inspect", lambda inspect: inspect(connection))
    with pytest.raises(
        operations_backup.BackupError, match="uncontained factor imports"
    ):
        database.source_snapshot(operations_backup.BackupLimits())
    assert not any("FROM artifacts" in query for query in connection.queries)


def snapshot(revision, schema):
    imports = schema == factor_backup.IMPORT_SCHEMA
    return {
        "schema_revision": revision,
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "factor_closure": {
            "schema_version": schema,
            "tables": {
                name: []
                for name in factor_backup.TABLE_NAMES
                + (factor_backup.IMPORT_TABLE_NAMES if imports else ())
            },
            "external_bindings": {
                name: [] for name in ("datasets", "strategies", "exposures")
            },
        },
    }


@pytest.mark.parametrize("revision", ["013_factor_import", "014_run_identity"])
def test_retention_requires_complete_import_closure_and_keeps_archive(revision):
    value = snapshot(revision, factor_backup.IMPORT_SCHEMA)
    objects = value["factor_closure"]["tables"]["factor_objects"]
    archive = (
        "factors/11111111-1111-4111-8111-111111111111/objects/" + "a" * 64 + ".zip"
    )
    objects.append(
        {"id": "archive-id", "object_kind": "package", "storage_key": archive}
    )
    value["factor_closure"]["tables"]["factor_import_envelopes"].append(
        {"archive_object_id": "archive-id"}
    )
    limits = operations_backup.BackupLimits()
    assert operations_retention._snapshot_manifest_keys(value, limits) == {archive}
    old = snapshot(revision, factor_backup.SCHEMA)
    with pytest.raises(operations_retention.RetentionError, match="closure revision"):
        operations_retention._snapshot_manifest_keys(old, limits)
    missing = deepcopy(value)
    del missing["factor_closure"]
    with pytest.raises(operations_retention.RetentionError):
        operations_retention._snapshot_manifest_keys(missing, limits)


@pytest.mark.parametrize("revision", ["015_future", None, [], {}])
def test_retention_rejects_unknown_capabilities_without_mutation(revision):
    value = snapshot(revision, factor_backup.IMPORT_SCHEMA)
    before = deepcopy(value)
    with pytest.raises(operations_retention.RetentionError):
        operations_retention._snapshot_manifest_keys(
            value, operations_backup.BackupLimits()
        )
    assert value == before


def test_neutral_revision_preserves_factor_and_historical_closure():
    old = capabilities_for("014_run_identity")
    neutral = capabilities_for("015_neutral_primitives")
    assert neutral.backup_supported and neutral.strategy_packages and neutral.note_pins
    assert neutral.factor_imports == old.factor_imports
    assert neutral.factor_closure_schema == old.factor_closure_schema
    assert old.research_closure_schema is None
    assert neutral.research_closure_schema == "mineagent.research-restore-closure.v1"
