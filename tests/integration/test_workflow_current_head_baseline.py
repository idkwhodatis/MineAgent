"""Actual032g finite workflow baseline, not candidate or full acceptance proof.

Own disposable databases and storage; the existing actual browser/RD/OE bridge
must succeed without simulated children, changed deadlines or SQL candidates.
Read-only graph checks follow success. Historical031 and restore gates remain
separate requirements and are not replaced by this current-schema diagnostic.
"""

import os

import pytest
from sqlalchemy import text
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from workflow_structured_stage_fixture import (
    actual_environment,
    command,
    create_workflow,
    no_engine_child,
    run_bridge,
)

pytestmark = pytest.mark.postgres


def test_actual_current032g_workflow_completes_with_readonly_graph_checks(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED") != "1":
        pytest.fail("Explicit actual032g workflow baseline required", pytrace=False)
    import test_backup_restore as legacy

    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "032g_schema_keyset_cost")
        storage = tmp_path / "current032g-workflow-source"
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage)
        with actual_environment(source, settings, monkeypatch) as env:
            # Authenticated controls before admission create no child or model call.
            for action, state in (
                ("pause_after_stage", "paused"),
                ("cancel", "cancelled"),
            ):
                wid = create_workflow(env)
                changed = command(env.client, env.headers, wid, action)
                assert changed.status_code == 200, changed.text
                stale = command(env.client, env.headers, wid, "cancel", revision=1)
                assert stale.status_code == 409, stale.text
                result = drive_one(
                    env.factory,
                    env.settings,
                    owner_id=env.account.user.id,
                    workflow_id=wid,
                )
                assert result.state in {"paused", "control_pending_or_terminal"}
                current = env.client.get(
                    f"/api/v1/research-workflows/{wid}", headers=env.headers
                )
                assert current.json()["status"] == state
                no_engine_child(env.factory, wid)
            assert env.rd_calls == env.oe_calls == []
            completed = run_bridge(env, source, tmp_path)
            call_counts = (len(env.rd_calls), len(env.oe_calls))
            with source.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                assert connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all() == [("032g_schema_keyset_cost",)]
                assert (
                    connection.execute(
                        text("SELECT status FROM research_workflows WHERE id=:id"),
                        {"id": completed.workflow_id},
                    ).scalar_one()
                    == "completed"
                )
                for run_id in (completed.rd.child_run_id, completed.oe_run_id):
                    assert (
                        connection.execute(
                            text("SELECT status FROM research_runs WHERE id=:id"),
                            {"id": run_id},
                        ).scalar_one()
                        == "succeeded"
                    )
                connection.execute(
                    text("SELECT public.mineagent_rd_provider_graph(:rid)"),
                    {"rid": completed.rd.child_run_id},
                ).scalar_one()
                connection.execute(
                    text("SELECT public.mineagent_oes_graph_check(:rid)"),
                    {"rid": completed.oe_run_id},
                ).scalar_one()
            assert (len(env.rd_calls), len(env.oe_calls)) == call_counts
    finally:
        source.dispose()
