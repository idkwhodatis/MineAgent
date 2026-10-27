"""Closed inventory tests; positive process/restore requires the real PG gate."""

import copy

import pytest

from backend.app import factor_analysis_backup as backup


def empty():
    return {
        "schema_version": backup.SCHEMA,
        "tables": {name: [] for name in backup.FIELDS},
    }


def test_empty_closed_inventory_has_no_private_paths():
    assert backup.manifest_keys(empty()) == set()
    assert set(backup.indexes(empty(), 100)) == set(backup.FIELDS)


@pytest.mark.parametrize(
    "change", ["version", "extra", "missing", "extra_table", "wrong_rows"]
)
def test_inventory_rejects_open_or_historical_grammar(change):
    value = copy.deepcopy(empty())
    if change == "version":
        value["schema_version"] = "mineagent.direct-analysis-restore-closure.v0"
    elif change == "extra":
        value["trust_me"] = True
    elif change == "missing":
        value["tables"].pop("dataset_versions")
    elif change == "extra_table":
        value["tables"]["unregistered"] = []
    else:
        value["tables"]["dataset_versions"] = {}
    with pytest.raises(ValueError):
        backup.indexes(value, 100)


def test_wrong_dependency_versions_cannot_accept_analysis():
    with pytest.raises(ValueError, match="version"):
        backup.validate_closure(
            empty(),
            research_closure={
                "schema_version": "mineagent.research-restore-closure.v6"
            },
            rd_source_closure={},
            execution_closure={},
            factor_closure={},
            read_bytes=lambda *a: b"",
            maximum_rows=100,
        )
