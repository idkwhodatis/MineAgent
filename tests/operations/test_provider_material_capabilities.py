"""027b is exact maintenance: no new closure or admission authority."""

import copy
import time
from typing import get_args

import pytest

from backend.app.operations_backup import REVISIONS, BackupLimits, _validate_references
from backend.app.operations_retention import _protected, _snapshot_manifest_keys
from backend.app.operations_schemas import BackupReceipt
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_openevolve_full_wiring import composed_snapshot

OLD = "027a_contract_repairs"
NEW = "027b_provider_material"


def test_material_revision_preserves_exact_all_capabilities_and_receipt_grammar():
    assert capabilities_for(NEW) == capabilities_for(OLD)
    assert {OLD, NEW} <= REVISIONS
    assert (
        set(get_args(BackupReceipt.model_fields["schema_revision"].annotation))
        == REVISIONS
    )


@pytest.mark.parametrize("revision", [OLD, NEW])
def test_complete_backup_retention_dispatch_preserves_prior_bytes(tmp_path, revision):
    snapshot = composed_snapshot(revision)
    _validate_references(tmp_path, [], snapshot, BackupLimits())
    assert _snapshot_manifest_keys(snapshot, BackupLimits()) == set()
    _, trees = _protected(snapshot, {}, {}, time.monotonic() + 10)
    assert "factors" in trees
    prior = composed_snapshot(OLD)
    assert {k: v for k, v in snapshot.items() if k != "schema_revision"} == {
        k: v for k, v in prior.items() if k != "schema_revision"
    }


@pytest.mark.parametrize(
    "field,bad",
    [
        ("research_closure", "mineagent.research-restore-closure.v10"),
        ("research_closure", "mineagent.research-restore-closure.v12"),
        ("rd_source_closure", "mineagent.rd-source-restore-closure.v5"),
        ("rd_source_closure", "mineagent.rd-source-restore-closure.v7"),
    ],
)
def test_new_revision_rejects_crossed_historical_and_future_closures(
    tmp_path, field, bad
):
    snapshot = copy.deepcopy(composed_snapshot(NEW))
    snapshot[field]["schema_version"] = bad
    with pytest.raises(ValueError):
        _validate_references(tmp_path, [], snapshot, BackupLimits())
    with pytest.raises(ValueError):
        _snapshot_manifest_keys(snapshot, BackupLimits())


@pytest.mark.parametrize(
    "revision",
    [
        "027b",
        "027b_provider_material_suffix",
        "027c_provider_material",
        "028_future",
        None,
    ],
)
def test_unknown_revision_remains_rejected(revision):
    with pytest.raises(ValueError):
        capabilities_for(revision)
