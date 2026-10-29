"""v11 shape is distinct; full physical dispatcher is deliberately not active."""

import copy
from uuid import uuid4

import pytest

from backend.app import research_backup as backup


def closure(schema=backup.SCHEMA_V13, config="mineagent.oe-structured-provider-run.v1"):
    run = {
        "id": str(uuid4()),
        "owner_id": str(uuid4()),
        "generation": 1,
        "purpose": "strategy_search",
        "engine": "openevolve",
        "status": "failed",
    }
    if schema == backup.SCHEMA_V13:
        run["config_schema_version"] = config
    return {
        "schema_version": schema,
        "tables": {name: [] for name in backup.FIELDS},
        "external_bindings": {"runs": [run], "attempts": []},
    }


def test_normal_and_old_oe_are_distinguished_by_actual_v11_field():
    for schema in (
        "mineagent.strategy-search.v1",
        "mineagent.oe-structured-provider-run.v1",
    ):
        value = closure(config=schema)
        indexed = backup._indexes(value, 100)
        assert next(iter(indexed["runs"].values()))["config_schema_version"] == schema


@pytest.mark.parametrize("value", [None, "", True, "mineagent.unknown-future.v12"])
def test_unknown_or_missing_oe_discriminator_is_not_legacy_fallback(value):
    with pytest.raises(ValueError):
        backup._indexes(closure(config=value), 100)


def test_v10_shape_stays_exact_and_v11_requires_new_field():
    old = closure(backup.SCHEMA_V10)
    backup._indexes(old, 100)
    extra = copy.deepcopy(old)
    extra["external_bindings"]["runs"][0]["config_schema_version"] = (
        "mineagent.strategy-search.v1"
    )
    with pytest.raises(ValueError, match="row fields"):
        backup._indexes(extra, 100)
    old["schema_version"] = backup.SCHEMA_V13
    with pytest.raises(ValueError, match="row fields"):
        backup._indexes(old, 100)


def test_shape_support_does_not_enable_incomplete_physical_restore():
    with pytest.raises(ValueError, match="successor final physical links"):
        backup.validate_closure(closure(), None, None, 100, inventory=[])
