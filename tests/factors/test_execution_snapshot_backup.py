"""Closed empty/incomplete graph checks only, not a positive restore drill."""

import copy

import pytest

from backend.app.execution_snapshot_backup import (
    FIELDS,
    SCHEMA,
    indexes,
    manifest_keys,
    validate_closure,
)


def empty():
    return {"schema_version": SCHEMA, "tables": {name: [] for name in FIELDS}}


def validate(value, *, research="mineagent.research-restore-closure.v6"):
    def unexpected(*_args):
        pytest.fail("Empty closure requested physical bytes")

    return validate_closure(
        value,
        unexpected,
        unexpected,
        100,
        inventory_rows=[],
        research_closure={"schema_version": research, "tables": {}},
        rd_source_closure={
            "schema_version": "mineagent.rd-source-restore-closure.v3",
            "tables": {},
        },
        native_closure={
            "schema_version": "mineagent.native-hdf-restore-closure.v2",
            "tables": {},
        },
    )


def test_empty_execution_graph_is_closed_without_claiming_execution():
    value = empty()
    assert indexes(value, 100) == {name: {} for name in FIELDS}
    assert manifest_keys(value) == set()
    assert validate(value) == {name: {} for name in FIELDS}


@pytest.mark.parametrize(
    "version",
    ["mineagent.research-restore-closure.v4", "mineagent.research-restore-closure.v5"],
)
def test_report_snapshot_cannot_reinterpret_historical_process_closure(version):
    with pytest.raises(ValueError, match="versions"):
        validate(empty(), research=version)


@pytest.mark.parametrize("table", list(FIELDS))
def test_missing_execution_table_is_not_empty_success(table):
    value = copy.deepcopy(empty())
    del value["tables"][table]
    with pytest.raises(ValueError, match="grammar"):
        validate(value)
