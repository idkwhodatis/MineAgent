"""Real whole-domain cold dispatcher with empty DB reader; no physical-run claim."""

import time

import pytest

from backend.app import (
    declared_upload_backup,
    oe_structured_operations,
    research_workflow_operations,
)
from backend.app.operations_backup import (
    BackupError,
    BackupLimits,
    _validate_references,
)
from backend.app.operations_retention import RetentionError, _protected
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_openevolve_full_wiring import (
    EmptyConnection,
    composed_snapshot,
)


def snapshot():
    revision = "031_workflow_structured_stage"
    value = composed_snapshot(revision)
    cap, db, limits = capabilities_for(revision), EmptyConnection(), BackupLimits()
    value["oe_structured_closure"] = oe_structured_operations.read_closure(
        db, cap, limits
    )
    value.update(research_workflow_operations.read_snapshot(db, cap, limits))
    value["declared_upload_closure"] = declared_upload_backup.read_closure(db, 100)
    return value


def test_complete_empty031_fanout_still_calls_all_domains(tmp_path):
    value = snapshot()
    _validate_references(tmp_path, [], value, BackupLimits())
    _, trees = _protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize(
    "domain,version",
    [
        ("research_closure", "mineagent.research-restore-closure.v15"),
        ("rd_source_closure", "mineagent.rd-source-restore-closure.v10"),
        ("workflow_closure", "mineagent.research-workflow-restore-closure.v1"),
        ("oe_structured_closure", "mineagent.oe-structured-restore-closure.v1"),
    ],
)
def test_no_partial031_domain_upgrade_can_pass(tmp_path, domain, version):
    value = snapshot()
    value[domain]["schema_version"] = version
    with pytest.raises(BackupError):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _protected(value, {}, {}, time.monotonic() + 10)


@pytest.mark.parametrize(
    "facts",
    [
        None,
        {},
        {"configs": {}, "measurements": {}},
        {"configs": {}, "measurements": {}, "epochs": {}, "claimed_complete": True},
    ],
)
def test_final_oe_checkpoint_join_requires_complete_physical_pass_result(facts):
    from backend.app.workflow_oe_backup import validate_checkpoints

    with pytest.raises(ValueError, match="Actual full normal OE closure facts"):
        validate_checkpoints(
            {"indexes": {}, "checkpoints": {}},
            {},
            {"tables": {}},
            {"tables": {}},
            facts,
        )
