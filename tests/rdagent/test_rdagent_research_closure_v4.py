from uuid import UUID

import pytest

from backend.app.operations_backup import BackupError, _key
from backend.app.research_backup import (
    FIELDS,
    FIELDS_V2,
    FIELDS_V4,
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    _indexes,
    closure_fields,
    validate_path,
)


def test_new_protocol_column_is_revision_explicit():
    assert closure_fields(SCHEMA) is FIELDS
    assert closure_fields(SCHEMA_V2) is FIELDS_V2
    assert closure_fields(SCHEMA_V3) is FIELDS_V2
    assert closure_fields(SCHEMA_V4) is FIELDS_V4
    assert "protocol" not in FIELDS_V2["research_model_operations"].split()
    assert "protocol" in FIELDS_V4["research_model_operations"].split()


def test_assembly_physical_path_needs_explicit_v4_capability():
    key = f"research-objects/{UUID(int=1)}/by-kind/factor_response_assembly/{'a' * 64}.json"
    for schema in (None, SCHEMA, SCHEMA_V2, SCHEMA_V3):
        with pytest.raises(ValueError):
            validate_path(key, schema=schema)
        with pytest.raises(BackupError):
            _key(key, storage=True, research_schema=schema)
    validate_path(key, schema=SCHEMA_V4)
    assert _key(key, storage=True, research_schema=SCHEMA_V4) == key


def test_v4_does_not_allow_legacy_context_under_rd_run():
    ids = [str(UUID(int=i)) for i in range(1, 5)]
    run = {
        "id": ids[1],
        "owner_id": ids[0],
        "generation": 1,
        "purpose": "factor_research",
        "engine": "rdagent",
        "status": "failed",
    }
    attempt = {
        "id": ids[2],
        "owner_id": ids[0],
        "run_id": ids[1],
        "generation": 1,
        "status": "failed",
        "sandbox_stopped": True,
        "finished_at": "2026-01-01T00:00:00Z",
    }
    closure = {
        "schema_version": SCHEMA_V4,
        "tables": {name: [] for name in FIELDS_V4},
        "external_bindings": {"runs": [run], "attempts": [attempt]},
    }
    _indexes(closure, 100)
    closure["tables"]["research_operation_contexts"].append(
        {
            "id": ids[3],
            "owner_id": ids[0],
            "run_id": ids[1],
            "attempt_id": ids[2],
            "generation": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "context": {
                "schema_version": "mineagent.neutral-fixture-context.v1",
                "scenario": "chat",
                "ordinal": 0,
            },
            "context_sha256": "a" * 64,
        }
    )
    with pytest.raises(ValueError, match="Run/context"):
        _indexes(closure, 100)
