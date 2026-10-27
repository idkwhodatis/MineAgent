"""Explicit025 dispatch and physical bounded reader contracts, not PG proof."""

import hashlib
import time
from dataclasses import asdict
from types import SimpleNamespace as NS
from typing import get_args

import pytest

from backend.app import structured_operations as m
from backend.app.operations_backup import BackupLimits
from backend.app.operations_schemas import BackupReceipt
from backend.app.schema_capabilities import capabilities_for

REVISION = "025_structured_factor_strategy"


def test_exact025_preserves024_and_adds_only_versioned_structured_capability():
    old, current = map(capabilities_for, ("024_openevolve_initial", REVISION))
    expected = asdict(old) | {
        "research_closure_schema": "mineagent.research-restore-closure.v9",
        "structured_strategy_schema": m.SCHEMA,
    }
    assert asdict(current) == expected
    assert old.structured_strategy_schema is None
    assert {"024_openevolve_initial", REVISION} <= set(
        get_args(BackupReceipt.model_fields["schema_revision"].annotation)
    )


def test_historical_schema_cannot_acquire_structured_evidence():
    old = capabilities_for("024_openevolve_initial")
    assert m.structured_strategy_manifest_keys({}, old) == set()
    with pytest.raises(ValueError, match="capability differs"):
        m.structured_strategy_manifest_keys({"structured_strategy_closure": {}}, old)


@pytest.mark.parametrize("closure", [None, {}, {"schema_version": "unknown"}])
def test_closed025_shape_version(closure):
    with pytest.raises(ValueError, match="version differs"):
        m.validate_structured_strategy_snapshot(
            {"structured_strategy_closure": closure},
            capabilities_for(REVISION),
            lambda *_: b"",
            10,
        )


def test_025_cannot_omit_domain_closure():
    with pytest.raises(ValueError, match="capability differs"):
        m.structured_strategy_manifest_keys({}, capabilities_for(REVISION))


def test_exact_dependencies_and_original_reader_forwarded(monkeypatch):
    from backend.app import structured_factor_backup as helper

    closure = {"schema_version": m.SCHEMA}
    snapshot = {
        "structured_strategy_closure": closure,
        "research_closure": {},
        "execution_closure": {},
        "direct_analysis_closure": {},
    }
    reader = lambda *_: b"actual"
    seen = []

    def validate(value, **kw):
        assert value is closure
        assert kw == {
            "research_closure": snapshot["research_closure"],
            "execution_closure": snapshot["execution_closure"],
            "direct_analysis_closure": snapshot["direct_analysis_closure"],
            "read_bytes": reader,
            "maximum_rows": 17,
        }
        seen.append(True)

    monkeypatch.setattr(helper, "validate_closure", validate)
    m.validate_structured_strategy_snapshot(
        snapshot, capabilities_for(REVISION), reader, 17
    )
    assert seen == [True]


def test_read_dispatch_keeps_both_limits_and_full_envelope_bound(monkeypatch):
    from backend.app import structured_factor_backup as helper

    closure = {"schema_version": m.SCHEMA, "tables": {}, "external_bindings": {}}
    calls = []

    def read(connection, rows, *, maximum_bytes):
        calls.append((connection, rows, maximum_bytes))
        return closure

    monkeypatch.setattr(helper, "read_closure", read)
    connection = object()
    assert (
        m.read_structured_strategy_closure(
            connection, capabilities_for(REVISION), BackupLimits()
        )
        is closure
    )
    assert calls == [
        (connection, BackupLimits().max_files, BackupLimits().max_manifest_bytes)
    ]
    with pytest.raises(ValueError, match="metadata budget"):
        m.read_structured_strategy_closure(
            connection, capabilities_for(REVISION), BackupLimits(max_manifest_bytes=1)
        )


@pytest.mark.parametrize("operation", ["backup", "retention"])
def test_actual_inventory_hash_reader_passes_only_after_predecessors(
    monkeypatch, tmp_path, operation
):
    from backend.app import operations_backup as backup
    from backend.app import operations_retention as retention

    cap = NS(
        **(
            asdict(capabilities_for("003_m3"))
            | {"structured_strategy_schema": m.SCHEMA}
        )
    )
    closure = {"schema_version": m.SCHEMA}
    value = {
        "schema_revision": REVISION,
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "structured_strategy_closure": closure,
    }
    key, raw = "datasets/retained.json", b'{"actual":true}'
    sha = hashlib.sha256(raw).hexdigest()
    inventory = [{"path": key, "sha256": sha, "size_bytes": len(raw)}]
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    monkeypatch.setattr(backup, "capabilities_for", lambda _: cap)
    monkeypatch.setattr(retention, "capabilities_for", lambda _: cap)
    # Dependency availability/order is independently tested above; this probe
    # verifies actual pinned physical reads through each operations dispatcher.
    seen = []

    def validate(snapshot, capabilities, reader, maximum_rows):
        assert snapshot["structured_strategy_closure"] is closure
        assert reader(key, len(raw)) == raw
        with pytest.raises((ValueError, backup.BackupError)):
            reader(key, 1)
        seen.append(True)

    monkeypatch.setattr(m, "validate_structured_strategy_snapshot", validate)
    if operation == "backup":
        backup._validate_references(tmp_path, inventory, value, BackupLimits())
    else:
        retention._protected(
            value, {key: inventory[0]}, {key: raw}, time.monotonic() + 10
        )
    assert seen == [True]


@pytest.mark.parametrize("operation", ["backup", "retention"])
def test_research_pending_flag_always_followed_by_structured_validation(
    monkeypatch, tmp_path, operation
):
    from backend.app import operations_backup as backup
    from backend.app import operations_retention as retention
    from backend.app import research_backup

    cap = NS(
        **(
            asdict(capabilities_for("003_m3"))
            | {
                "structured_strategy_schema": m.SCHEMA,
                "research_closure_schema": "mineagent.research-restore-closure.v9",
            }
        )
    )
    value = {
        "schema_revision": REVISION,
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "structured_strategy_closure": {"schema_version": m.SCHEMA},
        "research_closure": {"schema_version": cap.research_closure_schema},
    }
    monkeypatch.setattr(backup, "capabilities_for", lambda _: cap)
    monkeypatch.setattr(retention, "capabilities_for", lambda _: cap)
    events = []

    def research(*args, **kwargs):
        assert kwargs["structured_links_pending"] is True
        events.append("research_partial")

    def structured(*args, **kwargs):
        assert events == ["research_partial"]
        events.append("structured_final")
        raise ValueError("final graph rejected")

    monkeypatch.setattr(research_backup, "validate_closure", research)
    monkeypatch.setattr(m, "validate_structured_strategy_snapshot", structured)
    with pytest.raises((backup.BackupError, retention.RetentionError)):
        if operation == "backup":
            backup._validate_references(tmp_path, [], value, BackupLimits())
        else:
            retention._protected(value, {}, {}, time.monotonic() + 10)
    assert events == ["research_partial", "structured_final"]
