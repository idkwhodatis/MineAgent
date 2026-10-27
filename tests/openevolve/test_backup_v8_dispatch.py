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
