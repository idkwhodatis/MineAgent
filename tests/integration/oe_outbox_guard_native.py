"""Rollback-only outbox regression on the genuine successful workflow OE graph.

Call only after the existing native031 flow and historical031/032 restores have
passed, and after upgrading that same source to032f. No graph is manufactured or
replaced here, and no provider, process or runtime deadline is changed.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def assert_native_workflow_outbox_retained(engine, run_id):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.execute(
                text(
                    "SELECT config->>'schema_version',status FROM research_runs WHERE id=:id"
                ),
                {"id": run_id},
            ).one() == ("mineagent.oe-structured-provider-run.v2", "succeeded")
            query = text(
                "SELECT id,run_id,generation,created_at,dispatched_at,job_id FROM outbox_events WHERE run_id=:id AND generation=1"
            )
            before = connection.execute(query, {"id": run_id}).one()
            check = text("SELECT mineagent_oes_graph_check(:id)")
            connection.execute(check, {"id": run_id})
            savepoint = connection.begin_nested()
            try:
                deleted = connection.execute(
                    text("DELETE FROM outbox_events WHERE run_id=:id AND generation=1"),
                    {"id": run_id},
                )
                assert deleted.rowcount == 1
                with pytest.raises(DBAPIError) as failure:
                    connection.exec_driver_sql(
                        "SET CONSTRAINTS oes032f_outbox_delete IMMEDIATE"
                    )
                assert failure.value.orig.sqlstate == "P0001"
                assert (
                    failure.value.orig.diag.message_primary
                    == "Workflow OE requires durable original queue and budget"
                )
            finally:
                savepoint.rollback()
            assert connection.execute(query, {"id": run_id}).one() == before
            connection.execute(check, {"id": run_id})
        finally:
            transaction.rollback()
