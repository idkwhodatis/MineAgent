"""Bounded cursor contracts with inert rows; real PostgreSQL remains required."""

import pytest

from backend.app.rdagent_submission_backup import FIELDS, read_closure


class Result:
    def __init__(self, rows):
        self.rows = rows
        self.read = 0
        self.closed = False

    def scalars(self):
        for row in self.rows:
            self.read += 1
            yield row

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.results = []

    def execute(self, statement, parameters):
        options = statement.get_execution_options()
        assert options["stream_results"] is True and options["yield_per"] == 1
        assert parameters["limit"] > 1
        result = Result(next(self.batches))
        self.results.append(result)
        return result


def test_byte_overflow_closes_cursor_without_consuming_remaining_rows():
    connection = Connection([[{"raw": "x" * 100}, {"must_not_be_read": True}]])
    with pytest.raises(ValueError, match="closure bound"):
        read_closure(connection, 10, maximum_bytes=32)
    assert len(connection.results) == 1
    assert connection.results[0].read == 1 and connection.results[0].closed


def test_row_overflow_closes_cursor_and_does_not_read_next_table():
    connection = Connection([[{"row": 1}, {"row": 2}, {"row": 3}]])
    with pytest.raises(ValueError, match="closure bound"):
        read_closure(connection, 1, maximum_bytes=1000)
    assert len(connection.results) == 1
    assert connection.results[0].read == 2 and connection.results[0].closed


def test_success_preserves_table_order_and_closes_each_server_cursor():
    batches = [[{"row": 1}]] + [[] for _ in range(len(FIELDS) - 1)]
    connection = Connection(batches)
    result = read_closure(connection, 10, maximum_bytes=1000)
    assert list(result["tables"]) == list(FIELDS)
    assert list(result["tables"].values()) == batches
    assert len(connection.results) == len(FIELDS)
    assert all(item.closed for item in connection.results)


@pytest.mark.parametrize("rows,maximum", [(True, 10), (0, 10), (10, False), (10, 0)])
def test_invalid_limits_fail_before_database_access(rows, maximum):
    connection = Connection([])
    with pytest.raises(ValueError, match="closure bound"):
        read_closure(connection, rows, maximum_bytes=maximum)
    assert not connection.results
