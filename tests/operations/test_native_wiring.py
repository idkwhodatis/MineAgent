"""Versioned operation wiring, not a real PostgreSQL restore acceptance claim."""

import copy
import time

import pytest

from backend.app import (
    factor_backup,
    factor_native_backup,
    rdagent_backup,
    research_backup,
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


def snapshot():
    return {
        "schema_revision": "019_native_hdf_storage",
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "factor_closure": {
            "schema_version": factor_backup.IMPORT_SCHEMA,
            "tables": {
                k: []
                for k in factor_backup.TABLE_NAMES + factor_backup.IMPORT_TABLE_NAMES
            },
            "external_bindings": {
                k: [] for k in ("datasets", "strategies", "exposures")
            },
        },
        "research_closure": {
            "schema_version": research_backup.SCHEMA_V4,
            "tables": {k: [] for k in research_backup.FIELDS_V4},
            "external_bindings": {"runs": [], "attempts": []},
        },
        "rd_source_closure": {
            "schema_version": rdagent_backup.SCHEMA,
            "tables": {k: [] for k in rdagent_backup.FIELDS},
            "consumer_claims": [],
            "external_bindings": {"runs": [], "datasets": []},
        },
        "native_closure": {
            "schema_version": factor_native_backup.SCHEMA,
            "tables": {k: [] for k in factor_native_backup.FIELDS},
        },
    }


def test_empty019_snapshot_validates_all_closures_and_preserves_factor_tree(tmp_path):
    value = snapshot()
    _validate_references(tmp_path, [], value, BackupLimits())
    assert _snapshot_manifest_keys(value, BackupLimits()) == set()
    _exact, trees = _protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize(
    "revision",
    [
        "015_neutral_primitives",
        "016_process_versioning",
        "017_numeric_process_boundary",
        "018_rd_source_persistence",
    ],
)
def test_historical_capabilities_never_gain_native_registration(revision):
    assert capabilities_for(revision).native_closure_schema is None


@pytest.mark.parametrize("mutation", ["missing", "schema", "table", "extra"])
def test_corrupt_native_closure_rejected_by_backup_and_retention(tmp_path, mutation):
    value = snapshot()
    if mutation == "missing":
        value.pop("native_closure")
    if mutation == "schema":
        value["native_closure"]["schema_version"] = "unknown"
    if mutation == "table":
        value["native_closure"]["tables"].pop("factor_native_objects")
    if mutation == "extra":
        value["native_closure"]["tables"]["unknown"] = []
    with pytest.raises(BackupError, match="Native HDF"):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _snapshot_manifest_keys(value, BackupLimits())
    with pytest.raises(RetentionError, match="Native HDF"):
        _protected(value, {}, {}, time.monotonic() + 10)


def test_018_rejects_added_native_closure(tmp_path):
    value = copy.deepcopy(snapshot())
    value["schema_revision"] = "018_rd_source_persistence"
    with pytest.raises(BackupError, match="Native HDF"):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _snapshot_manifest_keys(value, BackupLimits())


def test_historical_native_capability_rejects_future_manifest_dispatch():
    import pytest

    from backend.app.factor_native_backup import FIELDS
    from backend.app.native_operations import native_manifest_keys
    from backend.app.schema_capabilities import capabilities_for

    snapshot = {
        "native_closure": {
            "schema_version": "mineagent.native-hdf-restore-closure.v2",
            "tables": {name: [] for name in FIELDS},
        }
    }
    with pytest.raises(ValueError, match="version"):
        native_manifest_keys(snapshot, capabilities_for("019_native_hdf_storage"))
