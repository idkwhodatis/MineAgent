"""Bounded dispatch/retained bytes tests, not activated024 or PG restore proof."""

import hashlib
import time
from dataclasses import asdict
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_operations as m
from backend.app.operations_backup import BackupLimits
from backend.app.schema_capabilities import SchemaCapabilities


def caps(schema=m.SCHEMA):
    values = asdict(SchemaCapabilities())
    values.update(
        openevolve_closure_schema=schema,
        research_closure_schema="mineagent.research-restore-closure.v8",
    )
    return NS(**values)


def snapshot():
    return {
        "schema_revision": "024_openevolve_initial",
        "postgres_major": 18,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "openevolve_closure": {"schema_version": m.SCHEMA},
        "research_closure": {
            "schema_version": "mineagent.research-restore-closure.v8",
            "tables": {
                "research_process_admissions": [],
                "research_process_receipts": [],
            },
        },
    }


def test_historical_cannot_acquire_field_or_future_capability():
    assert m.openevolve_closure({}, caps(None)) is None
    for body, capability in (
        (snapshot(), caps(None)),
        ({}, caps()),
        (snapshot(), caps("future")),
    ):
        with pytest.raises(ValueError):
            m.openevolve_closure(body, capability)


@pytest.mark.parametrize("value", [None, {}, {"schema_version": "future"}])
def test_closed_schema(value):
    with pytest.raises(ValueError):
        m.openevolve_closure({"openevolve_closure": value}, caps())


def test_read_exact_limits_and_whole_encoded_budget(monkeypatch):
    from backend.app import openevolve_backup_shape as shape

    seen = []
    value = {"schema_version": m.SCHEMA, "tables": {}, "external_bindings": {}}

    def read(connection, rows, **kwargs):
        seen.append((connection, rows, kwargs))
        return value

    monkeypatch.setattr(shape, "read_closure", read)
    limits = NS(max_files=7, max_manifest_bytes=1000)
    assert m.read_openevolve_closure("connection", caps(), limits) == value
    assert seen == [("connection", 7, {"maximum_bytes": 1000})]
    limits.max_manifest_bytes = 1
    with pytest.raises(ValueError, match="budget"):
        m.read_openevolve_closure(None, caps(), limits)


def extraction_snapshot():
    from runtimes.openevolve_evaluator.journal import NativeExtraction

    value = snapshot()
    owner, admission, run, attempt = (str(uuid4()) for _ in range(4))
    scope = {"owner_id": owner, "run_id": run, "attempt_id": attempt, "generation": 1}
    extraction = NativeExtraction(
        launch_sha256="a" * 64,
        identity_sha256="b" * 64,
        sha256="c" * 64,
        size_bytes=10,
        manifest_sha256="d" * 64,
        native_artifacts_sha256="e" * 64,
        protected_frame_complete=True,
        producer_quiesced=True,
    )
    value["research_closure"]["tables"] = {
        "research_process_admissions": [
            dict(
                scope, id=admission, profile="mineagent.openevolve-native-evaluator.v1"
            )
        ],
        "research_process_receipts": [
            dict(
                scope,
                admission_id=admission,
                kind="extraction",
                payload=extraction.model_dump(mode="json"),
            )
        ],
    }
    return value, f"research-processes/{owner}/{admission}/output/native-export.bin"


def test_binary_native_export_in_buffer_inventory_not_json():
    value, key = extraction_snapshot()
    assert m.openevolve_manifest_keys(value, caps()) == {key}


@pytest.mark.parametrize("fault", ["path", "scope", "duplicate"])
def test_invalid_extraction_inventory_rejected(fault):
    value, _ = extraction_snapshot()
    rows = value["research_closure"]["tables"]["research_process_receipts"]
    if fault == "path":
        rows[0]["payload"]["basename"] = "../other"
    elif fault == "scope":
        rows[0]["owner_id"] = str(uuid4())
    else:
        rows.append(rows[0].copy())
    with pytest.raises(ValueError):
        m.openevolve_manifest_keys(value, caps())


@pytest.mark.parametrize("operation", ["backup", "retention"])
def test_single_research_dispatch_receives_exact_closure_and_binary_reader(
    monkeypatch, tmp_path, operation
):
    from backend.app import operations_backup as backup
    from backend.app import operations_retention as retention
    from backend.app import research_backup

    value, key = extraction_snapshot()
    data = b"\xff\x00MAOE1-not-json"
    sha = hashlib.sha256(data).hexdigest()
    inventory = [{"path": key, "sha256": sha, "size_bytes": len(data)}]
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    monkeypatch.setattr(backup, "capabilities_for", lambda _: caps())
    monkeypatch.setattr(retention, "capabilities_for", lambda _: caps())
    seen = []

    def validate(closure, check, read_json, maximum_rows, **kwargs):
        assert kwargs["openevolve_closure"] is value["openevolve_closure"]
        assert kwargs["read_bytes"](key, sha, len(data)) == data
        with pytest.raises((ValueError, backup.BackupError, retention.RetentionError)):
            kwargs["read_bytes"](key, "0" * 64, len(data))
        seen.append(closure)

    monkeypatch.setattr(research_backup, "validate_closure", validate)
    if operation == "backup":
        backup._validate_references(tmp_path, inventory, value, BackupLimits())
    else:
        retention._protected(
            value, {key: inventory[0]}, {key: data}, time.monotonic() + 10
        )
    assert seen == [value["research_closure"]]


def test_retention_materializes_exact_binary_buffer_with_global_bound(
    monkeypatch, tmp_path
):
    from backend.app import operations_retention as retention
    from backend.app import research_backup

    value, key = extraction_snapshot()
    monkeypatch.setattr(retention, "capabilities_for", lambda _: caps())
    monkeypatch.setattr(research_backup, "manifest_keys", lambda _: set())
    monkeypatch.setattr(
        retention, "REVISIONS", (*retention.REVISIONS, "024_openevolve_initial")
    )
    keys = retention._snapshot_manifest_keys(value, BackupLimits())
    assert keys == {key}
    raw = b"\xff\x00MAOE1-not-json"
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    _, _, buffers = retention._scan(
        tmp_path,
        keys,
        BackupLimits(),
        time.monotonic() + 10,
        research_schema="mineagent.research-restore-closure.v8",
    )
    assert buffers == {key: raw}
    with pytest.raises(retention.RetentionError, match="Combined reference manifests"):
        retention._scan(
            tmp_path,
            keys,
            BackupLimits(max_manifest_bytes=1),
            time.monotonic() + 10,
            research_schema="mineagent.research-restore-closure.v8",
        )
    del value["openevolve_closure"]
    with pytest.raises(retention.RetentionError):
        retention._snapshot_manifest_keys(value, BackupLimits())
