"""032 exact retention inventory, empty real-reader composition not runtime proof."""

import pytest

from backend.app.normal_oe_note_backup import read_snapshot
from backend.app.operations_backup import BackupLimits
from backend.app.operations_retention import RetentionError, _snapshot_manifest_keys
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_openevolve_full_wiring import EmptyConnection
from tests.operations.test_upload030_cold_dispatch_matrix import composed_snapshot


def snapshot(revision="032_normal_oe_note_pins"):
    value = composed_snapshot(revision)
    value.update(
        read_snapshot(EmptyConnection(), capabilities_for(revision), BackupLimits())
    )
    return value


def test_normal_note_snapshot_is_admitted_by_retention_inventory():
    assert _snapshot_manifest_keys(snapshot(), BackupLimits()) == set()


@pytest.mark.parametrize(
    "change", ["missing", "extra", "old_capability", "unknown_schema"]
)
def test_normal_note_inventory_remains_exact(change):
    value = snapshot()
    if change == "missing":
        value.pop("normal_oe_note_closure")
    elif change == "extra":
        value["unknown_closure"] = {}
    elif change == "old_capability":
        value["schema_revision"] = "031_workflow_structured_stage"
    else:
        value["normal_oe_note_closure"]["schema_version"] = "unknown"
    with pytest.raises(RetentionError):
        _snapshot_manifest_keys(value, BackupLimits())


def test_historical_031_snapshot_remains_without_note_closure():
    assert (
        _snapshot_manifest_keys(
            snapshot("031_workflow_structured_stage"), BackupLimits()
        )
        == set()
    )
