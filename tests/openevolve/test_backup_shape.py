"""Frozen inventory and bounded cursor tests, not complete restore acceptance."""

from types import SimpleNamespace as NS

import pytest
from sqlalchemy.dialects.postgresql import UUID

from backend.app import openevolve_backup_shape as module
from backend.app.openevolve_backup_inventory import (
    FIELDS,
    FOREIGN_KEYS,
    PRIMARY_KEYS,
    SCHEMA,
    UUID_COLUMNS,
)
from backend.app.openevolve_persistence_ddl import tables


def empty():
    closure = {
        "schema_version": SCHEMA,
        "tables": {name: [] for name in FIELDS},
        "external_bindings": {name: [] for name in module.EXTERNAL_FIELDS},
    }
    external = (
        {remote for _, _, remote, _ in FOREIGN_KEYS}
        - set(FIELDS)
        - set(module.EXTERNAL_FIELDS)
        - {"research_runs", "execution_attempts"}
    )
    research = {
        "tables": {name: [] for name in external},
        "external_bindings": {"runs": [], "attempts": []},
    }
    return closure, research


def test_literal_inventory_exactly_matches_reviewed_thirty_two_tables():
    actual = tables()
    assert len(actual) == len(FIELDS) == 32
    assert {n: " ".join(c.name for c in t.columns) for n, t in actual.items()} == FIELDS
    assert {
        n: tuple(c.name for c in t.primary_key) for n, t in actual.items()
    } == PRIMARY_KEYS
    assert {
        n: tuple(c.name for c in t.columns if isinstance(c.type, UUID))
        for n, t in actual.items()
    } == UUID_COLUMNS
    keys = sorted(
        (
            n,
            tuple(e.parent.name for e in f.elements),
            f.elements[0].column.table.name,
            tuple(e.column.name for e in f.elements),
        )
        for n, t in actual.items()
        for f in t.foreign_key_constraints
    )
    assert tuple(keys) == FOREIGN_KEYS


def test_empty_shape_is_only_referential_not_physical_validation():
    closure, research = empty()
    assert len(module.validate_references(closure, research, maximum_rows=10)) >= 34


@pytest.mark.parametrize(
    "fault", ["version", "extra", "missing_table", "external", "rows"]
)
def test_closed_inventory_refuses_shape_changes(fault):
    closure, research = empty()
    if fault == "version":
        closure["schema_version"] = "old"
    elif fault == "extra":
        closure["extra"] = True
    elif fault == "missing_table":
        closure["tables"].pop(next(iter(FIELDS)))
    elif fault == "external":
        closure["external_bindings"] = {}
    else:
        closure["tables"][next(iter(FIELDS))] = {}
    with pytest.raises(ValueError):
        module.validate_references(closure, research, maximum_rows=10)


class Result:
    def __init__(self, rows):
        self.rows, self.count, self.closed = rows, 0, False

    def scalars(self):
        for row in self.rows:
            self.count += 1
            yield row

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, batches):
        self.batches, self.results = iter(batches), []

    def execute(self, statement, values):
        assert statement.get_execution_options() == {
            "stream_results": True,
            "yield_per": 1,
        }
        assert values["maximum"] >= 2
        result = Result(next(self.batches))
        self.results.append(result)
        return result


def test_streaming_stops_before_second_row_when_bytes_exceeded():
    connection = Connection([[{"raw": "x" * 1024}, {"never": "read"}]])
    with pytest.raises(ValueError, match="bounds"):
        module.read_closure(connection, 10, maximum_bytes=20)
    assert connection.results[0].count == 1 and connection.results[0].closed


def test_streaming_stops_after_first_overflow_row():
    connection = Connection([[{"a": 1}, {"a": 2}, {"a": 3}]])
    with pytest.raises(ValueError, match="bounds"):
        module.read_closure(connection, 1)
    assert connection.results[0].count == 2 and connection.results[0].closed


def test_empty_reader_closes_every_cursor_and_emits_exact_tables():
    connection = Connection([[] for _ in range(35)])
    assert module.read_closure(connection, 1) == empty()[0]
    assert len(connection.results) == 35 and all(r.closed for r in connection.results)


@pytest.mark.parametrize("rows,size", [(True, 100), (0, 100), (1, False), (1, 0)])
def test_invalid_limits_fail_before_database_access(rows, size):
    with pytest.raises(ValueError):
        module.read_closure(NS(), rows, maximum_bytes=size)


def test_literal_uniques_nullability_and_scalar_types_match_reviewed_schema():
    from sqlalchemy import Float, Integer, String, UniqueConstraint

    from backend.app.openevolve_backup_inventory import (
        FLOAT_COLUMNS,
        INTEGER_COLUMNS,
        NONNULL_COLUMNS,
        TEXT_COLUMNS,
        UNIQUE_KEYS,
    )

    actual = tables()
    assert UNIQUE_KEYS == {
        n: tuple(
            sorted(
                {
                    tuple(c.name for c in u.columns)
                    for u in t.constraints
                    if isinstance(u, UniqueConstraint)
                }
            )
        )
        for n, t in actual.items()
    }
    assert NONNULL_COLUMNS == {
        n: tuple(c.name for c in t.columns if not c.nullable) for n, t in actual.items()
    }
    assert INTEGER_COLUMNS == {
        n: tuple(c.name for c in t.columns if isinstance(c.type, Integer))
        for n, t in actual.items()
    }
    assert FLOAT_COLUMNS == {
        n: tuple(c.name for c in t.columns if isinstance(c.type, Float))
        for n, t in actual.items()
    }
    assert TEXT_COLUMNS == {
        n: {c.name: c.type.length for c in t.columns if isinstance(c.type, String)}
        for n, t in actual.items()
    }


def test_frozen_bigint_kernel_ticks_are_not_truncated_to_int32():
    from sqlalchemy import BigInteger
    from backend.app.openevolve_backup_inventory import BIGINT_COLUMNS

    actual = {
        name: tuple(c.name for c in table.columns if isinstance(c.type, BigInteger))
        for name, table in tables().items()
        if any(isinstance(c.type, BigInteger) for c in table.columns)
    }
    assert (
        BIGINT_COLUMNS
        == actual
        == {
            "search_worker_assignments": ("start_ticks",),
            "search_worker_expected_identities": ("start_ticks",),
        }
    )
