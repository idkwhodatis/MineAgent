"""Independent physical factor inventory and closed schema-version capabilities."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import factor_backup as factors
from backend.app import operations_backup as backup


def empty_closure():
    return {
        "schema_version": factors.SCHEMA,
        "tables": {name: [] for name in factors.TABLE_NAMES},
        "external_bindings": {"datasets": [], "strategies": [], "exposures": []},
    }


def validate(value):
    factors.validate_closure(value, lambda *a: None, lambda p: {}, 100)


def test_empty_factor_library_is_explicit_not_missing():
    validate(empty_closure())
    for key in ("tables", "external_bindings", "schema_version"):
        malformed = empty_closure()
        malformed.pop(key)
        with pytest.raises(ValueError):
            validate(malformed)


@pytest.mark.parametrize(
    "change", ["unknown_table", "missing_table", "unknown_version", "duplicate_object"]
)
def test_closed_restore_inventory_rejects_ambiguous_evidence(change):
    value = empty_closure()
    if change == "unknown_table":
        value["tables"]["future_evidence"] = []
    elif change == "missing_table":
        value["tables"].pop("factor_packages")
    elif change == "unknown_version":
        value["schema_version"] = "mineagent.factor-restore-closure.v2"
    else:
        obj = {"id": str(uuid4()), "owner_id": str(uuid4())}
        value["tables"]["factor_objects"] = [obj, deepcopy(obj)]
    with pytest.raises(ValueError):
        validate(value)


def test_physical_objects_are_verified_without_any_snapshot_or_run():
    value = empty_closure()
    owner = str(uuid4())
    obj = {
        "id": str(uuid4()),
        "owner_id": owner,
        "storage_key": f"factors/{owner}/objects/orphan.json",
        "sha256": "a" * 64,
        "size_bytes": 14,
        "object_kind": "native_evidence",
    }
    value["tables"]["factor_objects"] = [obj]
    seen = []
    factors.validate_closure(value, lambda *a: seen.append(a), lambda p: {}, 100)
    assert seen == [(obj["storage_key"], obj["sha256"], 14)]
    obj["storage_key"] = f"factors/{uuid4()}/objects/orphan.json"
    with pytest.raises(ValueError, match="ownership"):
        validate(value)


def test_inventory_row_budget_counts_all_tables():
    value = empty_closure()
    value["tables"]["factors"] = [
        {"id": str(uuid4()), "owner_id": str(uuid4())} for _ in range(2)
    ]
    with pytest.raises(ValueError, match="budget"):
        factors.validate_closure(value, lambda *a: None, lambda p: {}, 1)


def test_read_closure_fixed_tables_and_hard_limit():
    calls = []

    def execute(statement, parameters):
        calls.append((str(statement), parameters))
        return SimpleNamespace(scalars=list)

    result = factors.read_closure(SimpleNamespace(execute=execute), 17)
    assert result == empty_closure()
    assert len(calls) == len(factors.TABLE_NAMES) + 3
    assert all(parameters == {"limit": 18} for _, parameters in calls)
    assert all("LIMIT :limit" in query for query, _ in calls)


def test_old_and_new_revision_closure_capabilities_are_not_interchangeable(tmp_path):
    base = {
        "schema_revision": "011_note_pin_trigger",
        "references": [],
        "datasets": [],
        "checkpoints": [],
    }
    backup._validate_references(tmp_path, [], base, backup.BackupLimits())
    with pytest.raises(backup.BackupError, match="capability"):
        backup._validate_references(
            tmp_path,
            [],
            {**base, "factor_closure": empty_closure()},
            backup.BackupLimits(),
        )
    newer = {**base, "schema_revision": "012_factor_library"}
    with pytest.raises(backup.BackupError, match="capability"):
        backup._validate_references(tmp_path, [], newer, backup.BackupLimits())
    backup._validate_references(
        tmp_path,
        [],
        {**newer, "factor_closure": empty_closure()},
        backup.BackupLimits(),
    )


@pytest.mark.parametrize(
    "revision", ["012_factor_library", "013_factor_import", "014_run_identity"]
)
def test_current_revision_retains_legacy_note_and_package_inventory(
    monkeypatch, revision
):
    from backend.tests.test_backup_v1 import InspectionConnection, QueryResult

    class Connection(InspectionConnection):
        def execute(self, statement, parameters=None):
            result = super().execute(statement, parameters)
            if "to_jsonb" in str(statement):
                return SimpleNamespace(scalars=list)
            if "FROM research_note_evidence_pins" in str(statement):
                return QueryResult(
                    [
                        {
                            "path": "runs/historical/pinned.json",
                            "sha256": "a" * 64,
                            "size_bytes": 10,
                        }
                    ]
                )
            return result

    connection = Connection(revisions=[revision])
    database = backup.PostgresDatabase(
        "postgresql://operator@localhost/unit_inspection"
    )
    monkeypatch.setattr(database, "_inspect", lambda inspect: inspect(connection))
    snapshot = database.source_snapshot(backup.BackupLimits())
    expected = empty_closure()
    if revision != "012_factor_library":
        expected["schema_version"] = factors.IMPORT_SCHEMA
        expected["tables"].update({name: [] for name in factors.IMPORT_TABLE_NAMES})
    assert snapshot["factor_closure"] == expected
    assert snapshot["references"] == [
        {"path": "runs/historical/pinned.json", "sha256": "a" * 64, "size_bytes": 10}
    ]
    assert any("FROM strategy_packages" in query for query in connection.queries)
    assert any("FROM factor_objects" in query for query in connection.queries)
    assert any(
        "FROM research_note_evidence_pins" in query and "JOIN" not in query
        for query in connection.queries
    )


def test_unicode_evidence_uses_the_exact_persisted_canonical_contract():
    from packages.factors.contracts import canonical_bytes

    definition = {"name": "动量因子", "interpretation": "收益与波动率"}
    assert factors.canonical(definition) == canonical_bytes(definition)


def test_restore_rejects_missing_bundle_inventory():
    from packages.factors.contracts import digest

    value = empty_closure()
    definition = {
        "name": "动量因子",
        "members": [{"factor_version_id": str(uuid4()), "definition_sha256": "a" * 64}],
    }
    value["tables"]["factor_bundles"] = [
        {
            "id": str(uuid4()),
            "owner_id": str(uuid4()),
            "name": "动量因子",
            "definition": definition,
            "content_sha256": digest(definition),
        }
    ]
    with pytest.raises(ValueError, match="closure"):
        validate(value)


@pytest.mark.parametrize("revision", ["013_factor_import", "014_run_identity"])
def test_import_revision_cannot_downgrade_its_restore_closure(tmp_path, revision):
    snapshot = {
        "schema_revision": revision,
        "references": [],
        "datasets": [],
        "checkpoints": [],
        "factor_closure": empty_closure(),
    }
    with pytest.raises(backup.BackupError, match="closure"):
        backup._validate_references(tmp_path, [], snapshot, backup.BackupLimits())
    snapshot["factor_closure"]["schema_version"] = factors.IMPORT_SCHEMA
    snapshot["factor_closure"]["tables"].update(
        {key: [] for key in factors.IMPORT_TABLE_NAMES}
    )
    backup._validate_references(tmp_path, [], snapshot, backup.BackupLimits())
