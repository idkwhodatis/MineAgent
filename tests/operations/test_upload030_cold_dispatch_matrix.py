"""Production empty-domain accounting joins, not full nonempty restore proof."""

import pytest

from backend.app import research_backup as backup


def validate(research_version, source_version):
    closure = {
        "schema_version": f"mineagent.research-restore-closure.v{research_version}",
        "tables": {name: [] for name in backup.FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }
    from backend.app import oe_structured_backup, openevolve_backup_shape
    from tests.operations.test_openevolve_full_wiring import EmptyConnection

    options = {
        "inventory": [],
        "read_bytes": lambda *args: pytest.fail(
            "Empty closure requested physical bytes"
        ),
        "rd_source_closure": {
            "schema_version": f"mineagent.rd-source-restore-closure.v{source_version}",
            "external_bindings": {"runs": []},
        },
        "oe_structured_closure": oe_structured_backup.read_closure(
            EmptyConnection(), 100
        ),
        "openevolve_closure": openevolve_backup_shape.read_closure(
            EmptyConnection(), 100
        ),
        "structured_links_pending": True,
        "successor_links_pending": True,
        "seed_context": {},
    }
    if research_version in (14, 15):
        options.update(workflow_links_pending=True, workflow_context={})
    return backup.validate_closure(
        closure, lambda *args: None, lambda *args: None, 100, **options
    )


@pytest.mark.parametrize(
    "research_version,source_version", [(13, 8), (14, 9), (15, 10)]
)
def test_exact_accounting_source_pair(research_version, source_version):
    validate(research_version, source_version)


@pytest.mark.parametrize(
    "research_version,source_version",
    [(13, 9), (13, 10), (14, 8), (14, 10), (15, 8), (15, 9), (15, 11)],
)
def test_mixed_or_future_accounting_source_pair_rejected(
    research_version, source_version
):
    with pytest.raises(ValueError, match="Provider accounting"):
        validate(research_version, source_version)


def composed_snapshot(revision):
    from backend.app import (
        declared_upload_backup,
        oe_structured_operations,
        research_workflow_operations,
    )
    from backend.app.operations_backup import BackupLimits
    from backend.app.schema_capabilities import capabilities_for
    from tests.operations.test_openevolve_full_wiring import EmptyConnection
    from tests.operations.test_openevolve_full_wiring import (
        composed_snapshot as predecessor,
    )

    value = predecessor(revision)
    cap, db, limits = capabilities_for(revision), EmptyConnection(), BackupLimits()
    value["oe_structured_closure"] = oe_structured_operations.read_closure(
        db, cap, limits
    )
    value.update(research_workflow_operations.read_snapshot(db, cap, limits))
    if cap.declared_upload_schema:
        value["declared_upload_closure"] = declared_upload_backup.read_closure(db, 100)
    return value


@pytest.mark.parametrize(
    "revision",
    ["028_oe_structured_search", "029_research_workflow", "030_declared_upload_basket"],
)
def test_actual_full_cold_fanout_empty_reader_outputs(tmp_path, revision):
    import time

    from backend.app.operations_backup import BackupLimits, _validate_references
    from backend.app.operations_retention import _protected, _snapshot_manifest_keys

    value = composed_snapshot(revision)
    _validate_references(tmp_path, [], value, BackupLimits())
    assert _snapshot_manifest_keys(value, BackupLimits()) == set()
    _, trees = _protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize(
    "revision",
    ["028_oe_structured_search", "029_research_workflow", "030_declared_upload_basket"],
)
@pytest.mark.parametrize(
    "domain",
    [
        "research_closure",
        "rd_source_closure",
        "native_closure",
        "execution_closure",
        "direct_analysis_closure",
        "oe_structured_closure",
    ],
)
def test_full_dispatch_rejects_wrong_domain_versions(tmp_path, revision, domain):
    import time

    from backend.app.operations_backup import (
        BackupError,
        BackupLimits,
        _validate_references,
    )
    from backend.app.operations_retention import RetentionError, _protected

    value = composed_snapshot(revision)
    value[domain]["schema_version"] += ".future"
    with pytest.raises(BackupError):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _protected(value, {}, {}, time.monotonic() + 10)


@pytest.mark.parametrize(
    "domain", ["workflow_closure", "research_input_closure", "declared_upload_closure"]
)
def test_upload_full_dispatch_requires_exact_additional_domains(tmp_path, domain):
    import time

    from backend.app.operations_backup import (
        BackupError,
        BackupLimits,
        _validate_references,
    )
    from backend.app.operations_retention import RetentionError, _protected

    value = composed_snapshot("030_declared_upload_basket")
    value[domain]["schema_version"] += ".future"
    with pytest.raises(BackupError):
        _validate_references(tmp_path, [], value, BackupLimits())
    with pytest.raises(RetentionError):
        _protected(value, {}, {}, time.monotonic() + 10)
