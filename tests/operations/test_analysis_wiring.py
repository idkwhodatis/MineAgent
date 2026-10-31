"""Explicit operation dispatch contracts, never a database/restore proof."""

import pytest

from backend.app.schema_capabilities import SCHEMA_CAPABILITIES, capabilities_for


def test_analysis_revision_preserves_exact_historical_capabilities():
    current = capabilities_for("022_factor_analysis")
    assert current.backup_supported
    assert (
        current.direct_analysis_schema == "mineagent.direct-analysis-restore-closure.v1"
    )
    assert current.research_closure_schema == "mineagent.research-restore-closure.v7"
    assert current.rd_source_closure_schema == "mineagent.rd-source-restore-closure.v4"
    assert current.native_closure_schema == "mineagent.native-hdf-restore-closure.v3"
    assert (
        current.execution_snapshot_schema
        == "mineagent.execution-snapshot-restore-closure.v2"
    )
    for revision, historical in SCHEMA_CAPABILITIES.items():
        if revision not in {
            "022_factor_analysis",
            "023_rd_submissions",
            "024_openevolve_initial",
            "025_structured_factor_strategy",
            "026_rd_seeded_submissions",
            "027_rd_controller_v3",
            "027a_contract_repairs",
            "027b_provider_material",
            "027c_seeded_controller_v4",
            "028_oe_structured_search",
            "029_research_workflow",
            "030_declared_upload_basket",
            "031_workflow_structured_stage",
            "032_normal_oe_note_pins",
            "032a_rd_terminal_results",
        }:
            assert historical.direct_analysis_schema is None
    old = capabilities_for("021_execution_snapshots")
    assert old.research_closure_schema == "mineagent.research-restore-closure.v6"
    assert old.rd_source_closure_schema == "mineagent.rd-source-restore-closure.v3"
    assert old.native_closure_schema == "mineagent.native-hdf-restore-closure.v2"
    assert (
        old.execution_snapshot_schema
        == "mineagent.execution-snapshot-restore-closure.v1"
    )


@pytest.mark.parametrize(
    "revision", ["022", "022_factor_analysis_suffix", "023_future", None, 22]
)
def test_analysis_dispatch_never_infers_unknown_revision(revision):
    with pytest.raises(ValueError, match="Unsupported application schema revision"):
        capabilities_for(revision)


def test_historical_schema_cannot_acquire_analysis_closure():
    from backend.app.factor_analysis_operations import direct_analysis_manifest_keys

    old = capabilities_for("021_execution_snapshots")
    assert direct_analysis_manifest_keys({}, old) == set()
    with pytest.raises(ValueError, match="capability differs"):
        direct_analysis_manifest_keys({"direct_analysis_closure": {}}, old)


@pytest.mark.parametrize(
    "closure",
    [None, {}, {"schema_version": "mineagent.direct-analysis-restore-closure.v2"}],
)
def test_current_analysis_requires_exact_closed_schema(closure):
    from backend.app.factor_analysis_operations import direct_analysis_manifest_keys

    with pytest.raises(ValueError, match="version differs"):
        direct_analysis_manifest_keys(
            {"direct_analysis_closure": closure},
            capabilities_for("022_factor_analysis"),
        )


def test_current_analysis_cannot_omit_closure():
    from backend.app.factor_analysis_operations import validate_direct_analysis_snapshot

    with pytest.raises(ValueError, match="capability differs"):
        validate_direct_analysis_snapshot(
            {}, capabilities_for("022_factor_analysis"), lambda *_: b"", 10
        )


def test_dispatch_forwards_all_dependency_closures_without_read_authority_substitution(
    monkeypatch,
):
    import sys
    from types import ModuleType

    from backend.app.factor_analysis_operations import (
        SCHEMA,
        validate_direct_analysis_snapshot,
    )

    helper = ModuleType("backend.app.factor_analysis_backup")
    seen = {}

    def validate(closure, **kwargs):
        seen.update(kwargs)
        assert closure == {"schema_version": SCHEMA, "tables": {}}
        return {"checked": True}

    helper.validate_closure = validate
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    snapshot = {"direct_analysis_closure": {"schema_version": SCHEMA, "tables": {}}}
    for field in (
        "research_closure",
        "rd_source_closure",
        "execution_closure",
        "factor_closure",
    ):
        snapshot[field] = {"unique": field}
    reader = lambda *_: b"closed retained bytes"
    result = validate_direct_analysis_snapshot(
        snapshot, capabilities_for("022_factor_analysis"), reader, 17
    )
    assert result == {"checked": True}
    assert seen == {
        **{
            key: value
            for key, value in snapshot.items()
            if key != "direct_analysis_closure"
        },
        "read_bytes": reader,
        "maximum_rows": 17,
    }


def test_read_dispatch_bounds_whole_encoded_metadata(monkeypatch):
    from types import SimpleNamespace

    from backend.app import factor_analysis_backup as backup
    from backend.app.factor_analysis_operations import read_direct_analysis_closure

    closure = {
        "schema_version": backup.SCHEMA,
        "tables": {name: [] for name in backup.FIELDS},
    }
    monkeypatch.setattr(backup, "read_closure", lambda *a, **k: closure)
    with pytest.raises(ValueError, match="metadata budget"):
        read_direct_analysis_closure(
            None,
            capabilities_for("022_factor_analysis"),
            SimpleNamespace(max_files=100, max_manifest_bytes=1),
        )
    assert (
        read_direct_analysis_closure(
            None,
            capabilities_for("022_factor_analysis"),
            SimpleNamespace(max_files=100, max_manifest_bytes=10000),
        )
        == closure
    )


def test_empty_real_helper_manifest_dispatch_and_future_grammar_rejection():
    from backend.app import factor_analysis_backup as backup
    from backend.app.factor_analysis_operations import direct_analysis_manifest_keys

    closure = {
        "schema_version": backup.SCHEMA,
        "tables": {name: [] for name in backup.FIELDS},
    }
    snapshot = {"direct_analysis_closure": closure}
    assert (
        direct_analysis_manifest_keys(snapshot, capabilities_for("022_factor_analysis"))
        == set()
    )
    closure["tables"]["future_table"] = []
    with pytest.raises(ValueError):
        direct_analysis_manifest_keys(snapshot, capabilities_for("022_factor_analysis"))


@pytest.mark.parametrize("operation", ["backup", "retention"])
def test_analysis_semantic_validation_precedes_final_generation_links(
    monkeypatch, tmp_path, operation
):
    """Dispatcher doubles only; real versioned semantic/PG proof remains separate."""
    import time

    from backend.app import (
        execution_snapshot_operations,
        factor_analysis_operations,
        factor_backup,
        native_operations,
        rdagent_backup,
        rdagent_execution_links,
        research_backup,
    )
    from backend.app.operations_backup import BackupLimits, _validate_references
    from backend.app.operations_retention import _protected

    events = []
    monkeypatch.setattr(factor_backup, "validate_closure", lambda *a, **k: None)
    monkeypatch.setattr(
        research_backup, "validate_closure", lambda *a, **k: events.append("research")
    )

    def source(*args, **kwargs):
        assert kwargs == {
            "execution_links_pending": True,
            "configured_inputs_pending": True,
        }
        events.append("source")

    monkeypatch.setattr(rdagent_backup, "validate_closure", source)
    monkeypatch.setattr(
        native_operations,
        "validate_native_snapshot",
        lambda *a, **k: events.append("native"),
    )
    monkeypatch.setattr(
        execution_snapshot_operations,
        "validate_execution_snapshot",
        lambda *a, **k: events.append("execution"),
    )
    monkeypatch.setattr(
        factor_analysis_operations,
        "validate_direct_analysis_snapshot",
        lambda *a, **k: events.append("analysis"),
    )
    closure = {"schema_version": factor_analysis_operations.SCHEMA, "tables": {}}

    def links(*args, **kwargs):
        assert kwargs == {"direct_analysis_closure": closure}
        events.append("links")

    monkeypatch.setattr(
        rdagent_execution_links, "validate_execution_generation_links", links
    )
    snapshot = {
        "schema_revision": "022_factor_analysis",
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "factor_closure": {"schema_version": "mineagent.factor-restore-closure.v2"},
        "research_closure": {"schema_version": "mineagent.research-restore-closure.v7"},
        "rd_source_closure": {
            "schema_version": "mineagent.rd-source-restore-closure.v4"
        },
        "native_closure": {},
        "execution_closure": {},
        "direct_analysis_closure": closure,
    }
    if operation == "backup":
        _validate_references(tmp_path, [], snapshot, BackupLimits())
    else:
        _protected(snapshot, {}, {}, time.monotonic() + 10)
    assert events == ["research", "source", "native", "execution", "analysis", "links"]
