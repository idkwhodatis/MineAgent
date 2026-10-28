"""Explicit maintenance revision keeps old cold bytes, never launch authority."""

import copy
import time
from typing import get_args

import pytest

from backend.app.operations_backup import REVISIONS, BackupLimits, _validate_references
from backend.app.operations_retention import _protected, _snapshot_manifest_keys
from backend.app.operations_schemas import BackupReceipt
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_openevolve_full_wiring import composed_snapshot

OLD = "027_rd_controller_v3"
NEW = "027a_contract_repairs"


def test_explicit_maintenance_capabilities_and_receipt_remain_same_format():
    assert capabilities_for(NEW) == capabilities_for(OLD)
    assert (
        capabilities_for(NEW).research_closure_schema
        == "mineagent.research-restore-closure.v11"
    )
    assert (
        capabilities_for(NEW).rd_source_closure_schema
        == "mineagent.rd-source-restore-closure.v6"
    )
    assert {OLD, NEW} <= REVISIONS
    assert (
        set(get_args(BackupReceipt.model_fields["schema_revision"].annotation))
        == REVISIONS
    )


@pytest.mark.parametrize("revision", [OLD, NEW])
def test_actual_empty_backup_retention_fanout_preserves_all_domains(tmp_path, revision):
    snapshot = composed_snapshot(revision)
    _validate_references(tmp_path, [], snapshot, BackupLimits())
    assert _snapshot_manifest_keys(snapshot, BackupLimits()) == set()
    _, trees = _protected(snapshot, {}, {}, time.monotonic() + 10)
    assert "factors" in trees
    old = composed_snapshot(OLD)
    assert {k: v for k, v in snapshot.items() if k != "schema_revision"} == {
        k: v for k, v in old.items() if k != "schema_revision"
    }


@pytest.mark.parametrize(
    "field,bad",
    [
        ("research_closure", "mineagent.research-restore-closure.v12"),
        ("rd_source_closure", "mineagent.rd-source-restore-closure.v7"),
    ],
)
def test_maintenance_does_not_acquire_successor_closure(tmp_path, field, bad):
    snapshot = copy.deepcopy(composed_snapshot(NEW))
    snapshot[field]["schema_version"] = bad
    with pytest.raises(ValueError):
        _validate_references(tmp_path, [], snapshot, BackupLimits())
    with pytest.raises(ValueError):
        _snapshot_manifest_keys(snapshot, BackupLimits())


@pytest.mark.parametrize(
    "revision",
    [
        "027a",
        "027a_contract_repairs_suffix",
        "027b_contract_repairs",
        "028_future",
        None,
    ],
)
def test_unknown_revision_not_inferred(revision):
    with pytest.raises(ValueError):
        capabilities_for(revision)
