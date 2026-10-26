import ast
from pathlib import Path
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

    # The active source-retention integration fixture is not a stopped backup.
    # Exercise its exact bounded SQL-reference reader without importing PG setup.
    from sqlalchemy import text

    from backend.app import research_backup

    path = (
        Path(__file__).resolve().parents[1]
        / "integration/test_rdagent_source_backup_postgres.py"
    )
    tree = ast.parse(path.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "active_source_references"
    )
    namespace = {"text": text, "research_backup": research_backup}
    exec(  # noqa: S102 -- exact checked-in test helper, never user/provider code
        compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"),
        namespace,
    )
    read = namespace["active_source_references"]

    class Stream:
        def __init__(self, values):
            self.values = values
            self.closed = False

        def scalars(self):
            return self

        def __iter__(self):
            return iter(self.values)

        def close(self):
            self.closed = True

    class Connection:
        def __init__(self, values):
            self.values = values
            self.streams = []

        def execute(self, statement, params):
            assert set(statement._bindparams) == {"limit"}
            assert params["limit"] >= 1
            assert statement.get_execution_options()["yield_per"] == 1
            stream = Stream(self.values)
            self.streams.append(stream)
            return stream

    connection = Connection([])
    snapshot = read(connection)
    assert set(snapshot["tables"]) == set(FIELDS_V4)
    assert snapshot["schema_version"] == SCHEMA_V4
    assert set(snapshot["external_bindings"]) == {"runs", "attempts"}
    assert all(stream.closed for stream in connection.streams)
    for bounds in ({"maximum_rows": 0}, {"maximum_bytes": 0}):
        connection = Connection([{"id": "retained, not fabricated"}])
        with pytest.raises(ValueError, match="snapshot exceeds bounds"):
            read(connection, **bounds)
        assert all(stream.closed for stream in connection.streams)


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
