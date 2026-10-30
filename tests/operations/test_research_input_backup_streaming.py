"""Real SQLAlchemy connection option isolation; SQLite substitutes empty rows.

This is not a PostgreSQL data or multi-session restore acceptance test.
"""

import pytest
from sqlalchemy import create_engine, event, text

from backend.app.research_input_backup import FIELDS, read_closure


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
                    assert closure["external_bindings"] == {
                        "owners": [],
                        "listings": [],
                    }
                    assert len(seen) == len(FIELDS) + 2
            finally:
                event.remove(connection, "before_cursor_execute", intercept)
            assert connection.get_execution_options() == original
            result = connection.execute(text("SELECT 18"))
            assert not result.context.execution_options.get("stream_results", False)
            assert result.scalar_one() == 18
    finally:
        engine.dispose()


@pytest.mark.parametrize("streaming", [False, True])
def test_database_profile_uses_cursor_compatible_version_query(streaming):
    from sqlalchemy.dialects.postgresql import psycopg

    from backend.app.operations_backup import PostgresDatabase

    engine = create_engine("sqlite://")
    seen = []

    def intercept(connection, cursor, statement, parameters, context, executemany):
        assert statement.startswith("SELECT") or statement.lstrip().startswith("SELECT")
        assert bool(context.execution_options.get("stream_results")) is streaming
        seen.append(statement)
        if statement == "SELECT current_setting('server_version_num')":
            assert str(text(statement).compile(dialect=psycopg.dialect())) == statement
            return "SELECT '180006'", ()
        assert "count(*)" in statement
        return "SELECT 0", ()

    try:
        with engine.connect() as connection:
            if streaming:
                connection.execution_options(stream_results=True, yield_per=1)
            original = dict(connection.get_execution_options())
            event.listen(connection, "before_cursor_execute", intercept, retval=True)
            assert PostgresDatabase._profile(connection) == 18
            assert seen[-1] == "SELECT current_setting('server_version_num')"
            assert len(seen) == 9
            assert connection.get_execution_options() == original
    finally:
        engine.dispose()
