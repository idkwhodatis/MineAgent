"""Pinned 032h genuine RD→OE source browser and same-revision cold restore."""

import os

import pytest
from sqlalchemy import text
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from workflow_oe_reverse_fixture import observe_032h_oe_reverse
from workflow_structured_stage_fixture import (
    actual_environment,
    assert_restore,
    command,
    create_workflow,
    no_engine_child,
    run_bridge,
)

pytestmark = pytest.mark.postgres


def test_actual_032h_workflow_completes_with_cold_restore(
    drill_databases, tmp_path, monkeypatch, record_property
):
    if (
        os.environ.get("MINEAGENT_WORKFLOW_032H_REQUIRED") != "1"
        or os.environ.get("DATA_PROVIDER_MODE") != "fixture"
        or os.environ.get("MODEL_PROFILES_ENABLED") != "false"
        or os.environ.get("E2E_LIVE_PROVIDERS") != "0"
    ):
        pytest.fail(
            "Explicit isolated 032h actual workflow proof required", pytrace=False
        )
    import test_backup_restore as legacy

    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "032h_workflow_oe_run_guard")
        storage = tmp_path / "current032h-workflow-source"
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
            with observe_032h_oe_reverse(
                monkeypatch,
                record_property,
                enabled=os.environ.get("MINEAGENT_WORKFLOW_032H_REVERSE_DIAGNOSTIC")
                == "1",
            ):
                completed = run_bridge(env, source, tmp_path)
            call_counts = (len(env.rd_calls), len(env.oe_calls))
            with source.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                assert connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all() == [("032h_workflow_oe_run_guard",)]
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
            record_property("current_032h_bridge", "passed")
            assert_restore(
                drill_databases,
                tmp_path,
                source,
                env,
                completed,
                expected_revision="032h_workflow_oe_run_guard",
            )
            assert (len(env.rd_calls), len(env.oe_calls)) == call_counts
            record_property("current_032h_cold_restore", "passed")
    finally:
        source.dispose()
