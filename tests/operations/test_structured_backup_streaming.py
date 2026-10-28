"""Real SQLAlchemy connection option isolation; SQLite substitutes empty rows.

This is not a PostgreSQL data or multi-session restore acceptance test.
"""

import pytest
from sqlalchemy import create_engine, event, text

from backend.app.structured_factor_backup import FIELDS, read_closure


@pytest.mark.parametrize("fail", [False, True])
def test_streaming_is_statement_local_and_preserves_connection_after_read(fail):
    engine = create_engine("sqlite://")
    seen = []

    def intercept(connection, cursor, statement, parameters, context, executemany):
        assert statement.startswith("SELECT to_jsonb(t)")
        assert context.execution_options["stream_results"] is True
        assert context.execution_options["yield_per"] == 1
        seen.append(statement)
        if fail:
            raise ValueError("injected database read failure")
        return "SELECT NULL WHERE 0", ()

    try:
        with engine.connect() as connection:
            original = dict(connection.get_execution_options())
            event.listen(connection, "before_cursor_execute", intercept, retval=True)
            try:
                if fail:
                    with pytest.raises(
                        ValueError, match="injected database read failure"
                    ):
                        read_closure(connection, 10)
                else:
                    closure = read_closure(connection, 10)
                    assert set(closure["tables"]) == set(FIELDS)
                    assert closure["external_bindings"] == {"research_runs": []}
                    assert len(seen) == len(FIELDS) + 1
            finally:
                event.remove(connection, "before_cursor_execute", intercept)
            assert connection.get_execution_options() == original
            result = connection.execute(text("SELECT 18"))
            assert not result.context.execution_options.get("stream_results", False)
            assert result.scalar_one() == 18
    finally:
        engine.dispose()
