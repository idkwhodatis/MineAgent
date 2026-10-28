"""Explicit future physical grammar never activates incomplete domain closure."""

from uuid import uuid4

import pytest

from backend.app import research_backup as backup
from backend.app.schema_capabilities import capabilities_for


@pytest.mark.parametrize("schema", [backup.SCHEMA_V6, backup.SCHEMA_V7])
def test_prior_paths_stay_closed(schema):
    owner, admission = uuid4(), uuid4()
    paths = [
        f"research-processes/{owner}/{admission}/output/native-export.bin",
        f"research-processes/{owner}/{admission}/manifest.json",
        f"research-objects/{owner}/by-kind/search_worker_outcome/{'a' * 64}.json",
    ]
    for path in paths:
        with pytest.raises(ValueError):
            backup.validate_path(path, schema=schema)
        backup.validate_path(path, schema=backup.SCHEMA_V8)


def test_full_revision_declares_separate_domain_closure_not_component_only():
    capability = capabilities_for("024_openevolve_initial")
    assert capability.backup_supported
    assert capability.research_closure_schema == backup.SCHEMA_V8
    assert (
        capability.openevolve_closure_schema
        == "mineagent.openevolve-restore-closure.v1"
    )
    assert backup.closure_fields(backup.SCHEMA_V8) == backup.FIELDS_V4
    with pytest.raises(ValueError, match="full separate typed domain closure"):
        backup.validate_closure(
            {"schema_version": backup.SCHEMA_V8}, None, None, 100, inventory=[]
        )
    with pytest.raises(ValueError):
        backup.closure_fields("mineagent.research-restore-closure.v11")


@pytest.mark.parametrize(
    "schema", [backup.SCHEMA_V8, backup.SCHEMA_V9, backup.SCHEMA_V10]
)
def test_native_manifest_path_does_not_allow_other_root_files(schema):
    prefix = f"research-processes/{uuid4()}/{uuid4()}/"
    backup.validate_path(prefix + "manifest.json", schema=schema)
    for suffix in ("other.json", "manifest.json/extra", "inputs/manifest.json"):
        with pytest.raises(ValueError):
            backup.validate_path(prefix + suffix, schema=schema)


@pytest.mark.parametrize(
    "schema", [backup.SCHEMA_V8, backup.SCHEMA_V9, backup.SCHEMA_V10]
)
@pytest.mark.parametrize("profile", ["native", "controller"])
def test_retention_manifest_routing_matches_native_production_layout(
    schema, profile, monkeypatch
):
    # Routing-only projection: exact row graph/physical bytes are exercised by
    # test_process_backup, not manufactured by this manifest-key selector test.
    monkeypatch.setattr(backup, "_indexes", lambda *_: None)
    owner, identity = str(uuid4()), str(uuid4())
    closure = {
        "schema_version": schema,
        "tables": {key: [] for key in backup.FIELDS_V4},
    }
    closure["tables"]["research_process_admissions"] = [
        {
            "id": identity,
            "owner_id": owner,
            "profile": "mineagent.openevolve-native-evaluator.v1"
            if profile == "native"
            else "mineagent.openevolve-orchestrator.v1",
        }
    ]
    prefix = f"research-processes/{owner}/{identity}/"
    expected = {
        prefix + "control/" + name + ".json" for name in ("launch", "identity", "stop")
    }
    if profile == "native":
        expected.add(prefix + "manifest.json")
    assert backup.manifest_keys(closure) == expected
