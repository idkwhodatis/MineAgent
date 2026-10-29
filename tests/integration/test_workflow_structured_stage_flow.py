"""Required actual031 HTTP-created RD→released checkpoint→OE→cold restore gate.

After that proof, the same real graph is upgraded to032 for typed notes and a
second fresh restore; no extra model/engine calls. The successful root is browser-created. Runtime tests never skip when their mandatory flag or
pinned images/SDK are absent. Local collection is not execution acceptance.
"""

import os

import pytest
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from workflow_structured_stage_fixture import (
    actual_environment,
    assert_restore,
    command,
    create_workflow,
    no_engine_child,
    run_bridge,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def note_restore_databases():
    """An independently owned empty destination, with normal fixture cleanup."""
    yield from drill_databases.__wrapped__()


def test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore(
    drill_databases, note_restore_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_WORKFLOW_STRUCTURED_FLOW_REQUIRED") != "1":
        pytest.fail("Explicit actual031 workflow/OE gate required", pytrace=False)
    import test_backup_restore as legacy

    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "031_workflow_structured_stage")
        storage = tmp_path / "workflow-oe-source"
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
            assert_restore(drill_databases, tmp_path, source, env, completed)
            # Preserve the exact031 non-disclosure/restore proof above before
            # activating new note grammar on the same actual successful graph.
            from dataclasses import replace

            from normal_oe_note_flow import (
                assert_notes_restore,
                assert_unavailable_before032,
                create_released_notes,
            )

            assert_unavailable_before032(env, completed)
            call_counts = (len(env.rd_calls), len(env.oe_calls))
            legacy._upgrade(source, "032_normal_oe_note_pins")
            notes = create_released_notes(env, completed)
            note_root = tmp_path / "normal-note-roundtrip"
            note_root.mkdir(mode=0o700)
            assert_notes_restore(
                replace(note_restore_databases, source=drill_databases.source),
                note_root,
                source,
                env,
                completed,
                notes,
            )
            assert (len(env.rd_calls), len(env.oe_calls)) == call_counts
    finally:
        source.dispose()
