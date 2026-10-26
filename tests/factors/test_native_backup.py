"""Local opaque inventory/closed shape tests, not actual PostgreSQL cold restore."""

import copy
import hashlib
from uuid import uuid4

import pytest

from backend.app import factor_native_backup as backup
from backend.app.factor_native_models import (
    NativeHDFObject,
    NativeHDFOutcome,
    NativeHDFPublication,
    NativeNormalizerInput,
)


def empty():
    return {
        "schema_version": backup.SCHEMA,
        "tables": {name: [] for name in backup.FIELDS},
    }


def validate(closure, inventory=()):
    def reject_json(key):
        pytest.fail("Opaque orphan was passed to JSON parser: " + key)

    backup.validate_closure(
        closure,
        lambda *args: None,
        reject_json,
        100,
        inventory=inventory,
        research_closure={"tables": {}},
        rd_source_closure={"tables": {}},
    )


def test_closed_native_column_inventory_matches_explicit_orm():
    for model in (
        NativeHDFObject,
        NativeHDFOutcome,
        NativeHDFPublication,
        NativeNormalizerInput,
    ):
        assert set(backup.FIELDS[model.__tablename__].split()) == set(
            model.__table__.columns.keys()
        )


def test_empty_native_closure_retains_actual_opaque_orphan_and_temp(tmp_path):
    owner = uuid4()
    data = b"not parsed as HDF"
    sha = hashlib.sha256(data).hexdigest()
    keys = [
        f"factors/{owner}/objects/{sha}.h5",
        f"factors/{owner}/objects/.{sha}.h5.{uuid4().hex}.tmp",
    ]
    inventory = []
    for key in keys:
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        inventory.append({"path": key, "sha256": sha, "size_bytes": len(data)})
    validate(empty(), inventory)
    assert all((tmp_path / key).read_bytes() == data for key in keys)
    assert backup.manifest_keys(empty()) == set()


def test_native_closure_does_not_claim_unrelated_legacy_hdf_namespace():
    validate(
        empty(), [{"path": "artifacts/legacy.h5", "sha256": "a" * 64, "size_bytes": 42}]
    )


@pytest.mark.parametrize(
    "name", ["schema", "missing_table", "extra_table", "extra_field"]
)
def test_native_closure_rejects_version_or_inventory_drift(name):
    value = copy.deepcopy(empty())
    if name == "schema":
        value["schema_version"] = "mineagent.native-hdf-restore-closure.v0"
    if name == "missing_table":
        value["tables"].pop("factor_native_outcomes")
    if name == "extra_table":
        value["tables"]["unrecognized"] = []
    if name == "extra_field":
        value["claimed_verified"] = True
    with pytest.raises(ValueError):
        validate(value)


def test_unknown_native_journal_is_recovery_barrier_not_committed_outcome():
    key = f"factors/{uuid4()}/native-journals/{uuid4()}.json"
    with pytest.raises(ValueError, match="Unadmitted"):
        validate(empty(), [{"path": key, "sha256": "a" * 64, "size_bytes": 2}])
