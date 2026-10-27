"""Cursor/budget tests use inert rows, not PostgreSQL or semantic restore proof."""

import pytest

from backend.app import structured_factor_backup as backup
from packages.factors.contracts import canonical_bytes


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.consumed = 0
        self.closed = False

    def scalars(self):
        for row in self.rows:
            self.consumed += 1
            yield row

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.cursors = []

    def execution_options(self, **kwargs):
        assert kwargs == {"stream_results": True, "yield_per": 1}
        return self

    def execute(self, statement, params):
        assert params["limit"] > 1
        assert "LIMIT :limit" in str(statement)
        cursor = Cursor(next(self.batches))
        self.cursors.append(cursor)
        return cursor


def test_empty_inventory_closes_every_stream_and_counts_envelope():
    connection = Connection([[] for _ in (*backup.FIELDS, "research_runs")])
    closure = backup.read_closure(connection, 1)
    assert closure == {
        "schema_version": backup.SCHEMA,
        "tables": {k: [] for k in backup.FIELDS},
        "external_bindings": {"research_runs": []},
    }
    assert all(cursor.closed for cursor in connection.cursors)
    assert backup.indexes(closure, 1) == {
        k: {} for k in (*backup.FIELDS, "research_runs")
    }
    with pytest.raises(ValueError, match="envelope bound"):
        backup.read_closure(
            Connection([[] for _ in (*backup.FIELDS, "research_runs")]),
            1,
            maximum_bytes=1,
        )


def test_row_budget_is_global_and_stops_before_materializing_remaining_rows():
    connection = Connection([[{"a": 1}], [{"b": 2}, {"c": 3}]])
    with pytest.raises(ValueError, match="closure bound"):
        backup.read_closure(connection, 1)
    assert [cursor.consumed for cursor in connection.cursors] == [1, 1]
    assert all(cursor.closed for cursor in connection.cursors)


def test_raw_text_encoded_bytes_are_charged_before_appending_next_row():
    row = {"raw": '\\"' * 100}
    connection = Connection([[row, {"unread": True}]])
    with pytest.raises(ValueError, match="closure bound"):
        backup.read_closure(connection, 5, maximum_bytes=len(canonical_bytes(row)))
    assert connection.cursors[0].consumed == 1
    assert connection.cursors[0].closed


def test_serializer_failure_closes_server_cursor():
    connection = Connection([[{"bad": object()}]])
    with pytest.raises(TypeError):
        backup.read_closure(connection, 5)
    assert connection.cursors[0].closed


@pytest.mark.parametrize("limit", [True, 0, -1, 100001])
def test_invalid_row_budget_never_queries(limit):
    connection = Connection([])
    with pytest.raises(ValueError, match="bound"):
        backup.read_closure(connection, limit)
    assert connection.cursors == []


def test_unregistered_predecessor_is_explicitly_closed_even_for_empty_graph():
    assert backup.RESEARCH_SCHEMA == "mineagent.research-restore-closure.v9"
    for schema in (None, "mineagent.research-restore-closure.v8", "future"):
        with pytest.raises(ValueError, match="Exact structured research"):
            backup.validate_closure(
                {},
                research_closure={"schema_version": schema},
                execution_closure={},
                direct_analysis_closure={},
                read_bytes=lambda *_: pytest.fail("must not read"),
            )


def test_real_empty_successor_closure_joins_actual_shared_run_location():
    closure = backup.read_closure(
        Connection([[] for _ in (*backup.FIELDS, "research_runs")]), 1
    )
    research = {
        "schema_version": backup.RESEARCH_SCHEMA,
        "external_bindings": {"runs": []},
        "tables": {
            key: []
            for key in (
                "research_process_admissions",
                "research_process_receipts",
                "research_budget_reservations",
                "research_budget_settlements",
                "research_process_states",
            )
        },
    }
    execution = {
        "tables": {
            "factor_execution_snapshot_publications": [],
            "factor_execution_snapshot_outcomes": [],
        }
    }
    direct = {"tables": {"dataset_versions": [], "data_usage_exposures": []}}
    result = backup.validate_closure(
        closure,
        research_closure=research,
        execution_closure=execution,
        direct_analysis_closure=direct,
        read_bytes=lambda *_: pytest.fail("empty closure must not read"),
    )
    assert result == {key: {} for key in (*backup.FIELDS, "research_runs")}
    research["external_bindings"]["runs"].append(
        {"id": "unbound", "engine": "structured"}
    )
    with pytest.raises(ValueError, match="run inventory"):
        backup.validate_closure(
            closure,
            research_closure=research,
            execution_closure=execution,
            direct_analysis_closure=direct,
            read_bytes=lambda *_: b"",
        )
