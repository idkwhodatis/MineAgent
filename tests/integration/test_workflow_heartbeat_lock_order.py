"""Real finite-workflow metadata, not completed/native graph acceptance.

Use original API admission and claim, real heartbeat and unmodified deferred
triggers on031/032g. No queue/controller/SDK runs; no fabricated attempt. Two
connections schedule crossed locks explicitly rather than relying on a race.
"""

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from workflow_structured_stage_fixture import actual_environment, create_workflow

pytestmark = pytest.mark.postgres


def _owner_busy(error):
    assert error.orig.sqlstate == "P0001"
    assert error.orig.diag.message_primary == "workflow owner is busy"


def _run_attempt(session, fence):
    session.execute(
        text("SELECT id FROM research_runs WHERE id=:id FOR UPDATE NOWAIT"),
        {"id": fence.run_id},
    ).scalar_one()
    session.execute(
        text("SELECT id FROM execution_attempts WHERE id=:id FOR UPDATE NOWAIT"),
        {"id": fence.attempt_id},
    ).scalar_one()


@pytest.mark.parametrize(
    "revision",
    ["031_workflow_structured_stage", "032g_schema_keyset_cost"],
    ids=["031", "032g"],
)
def test_real_workflow_heartbeat_crossed_owner_lock_fails_closed(
    drill_databases, tmp_path, monkeypatch, revision
):
    if os.environ.get("MINEAGENT_HEARTBEAT_ORDER_REQUIRED") != "1":
        pytest.fail(
            "Explicit finite-workflow heartbeat order probe required", pytrace=False
        )
    import test_backup_restore as legacy
    from rd_completed_graph_probe import _require_unchanged, _snapshot

    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from backend.app.research_lifecycle import _try_neutral_lock
    from backend.app.services import heartbeat
    from workers.research.lease import AttemptLease
    from workers.research.rdagent_queue import claim_configured_run
    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, revision)
        storage = tmp_path / "heartbeat-order-source"
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(source, storage)
        with actual_environment(source, settings, monkeypatch) as env:
            assert env.settings.lease_seconds == 120
            assert env.settings.heartbeat_seconds == 1
            wid = create_workflow(env)
            owner = env.account.user.id
            assert (
                drive_one(
                    env.factory, env.settings, owner_id=owner, workflow_id=wid
                ).state
                == "child_queued"
            )
            with env.factory() as session:
                runs = session.scalars(
                    select(FactorWorkflowSubmission.run_id)
                    .filter_by(workflow_id=wid)
                    .limit(2)
                ).all()
                assert len(runs) == 1
            fence = claim_configured_run(
                env.factory, runs[0], 1, "heartbeat-order-owned-fixture", env.settings
            )
            assert fence is not None
            assert env.rd_calls == env.oe_calls == []
            with source.connect() as c:
                assert c.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all() == [(revision,)]
                assert (
                    c.execute(
                        text(
                            "SELECT plan_canonical::jsonb->>'schema_version' FROM research_workflows WHERE id=:id"
                        ),
                        {"id": wid},
                    ).scalar_one()
                    == "mineagent.rd-structured-guidance-workflow-plan.v1"
                )
                assert c.execute(
                    text(
                        "SELECT config->>'schema_version',status FROM research_runs WHERE id=:id"
                    ),
                    {"id": fence.run_id},
                ).one() == ("mineagent.factor-research.v3", "running")
                assert (
                    c.exec_driver_sql(
                        "SELECT count(*) FROM research_model_operations"
                    ).scalar_one()
                    == 0
                )
                assert (
                    c.exec_driver_sql(
                        "SELECT count(*) FROM research_process_admissions"
                    ).scalar_one()
                    == 0
                )

            # Positive control uses the actual producer, without starting its
            # asynchronous maintenance thread or changing its lease semantics.
            lease = AttemptLease(
                env.factory,
                env.settings,
                fence.attempt_id,
                fence.generation,
                fence.lease_token,
                "heartbeat-order-owned-fixture",
            )
            assert lease.thread.ident is None
            assert lease.pulse(force=True) is None
            assert not lease.lost.is_set() and not lease.cancelled.is_set()
            assert lease.last_pulse > 0
            before = _snapshot(source)

            with (
                source.connect() as a,
                source.connect() as b,
                Session(bind=a) as owner_session,
                Session(bind=b) as heartbeat_session,
            ):
                owner_tx, heartbeat_tx = (
                    owner_session.begin(),
                    heartbeat_session.begin(),
                )
                try:
                    owner_session.execute(text("SET LOCAL lock_timeout='2s'"))
                    heartbeat_session.execute(text("SET LOCAL lock_timeout='2s'"))
                    _try_neutral_lock(owner_session, f"submit:{owner}")
                    # Original heartbeat obtains Run then Attempt, but not owner.
                    assert (
                        heartbeat(
                            heartbeat_session,
                            fence.attempt_id,
                            fence.generation,
                            fence.lease_token,
                            env.settings,
                        )
                        is False
                    )
                    heartbeat_session.flush()
                    with (
                        pytest.raises(DBAPIError) as caught,
                        owner_session.begin_nested(),
                    ):
                        _run_attempt(owner_session, fence)
                    assert caught.value.orig.sqlstate == "55P03"
                    # Exact installed deferred guard, not a direct validator call.
                    with (
                        pytest.raises(DBAPIError) as caught,
                        heartbeat_session.begin_nested(),
                    ):
                        heartbeat_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    _owner_busy(caught.value)
                    heartbeat_tx.rollback()
                    # Original Owner -> Run -> Attempt order succeeds once the
                    # competing heartbeat releases its row locks. Roll back it too.
                    _run_attempt(owner_session, fence)
                    assert (
                        heartbeat(
                            owner_session,
                            fence.attempt_id,
                            fence.generation,
                            fence.lease_token,
                            env.settings,
                        )
                        is False
                    )
                    owner_session.flush()
                    owner_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                finally:
                    if heartbeat_tx.is_active:
                        heartbeat_tx.rollback()
                    if owner_tx.is_active:
                        owner_tx.rollback()
            _require_unchanged(source, before)

            # Actual pulse commits naturally, hits the same original deferred
            # guard, and revokes its own authority. No exception is simulated.
            prior_success = lease.last_pulse
            with (
                source.connect() as a,
                Session(bind=a) as owner_session,
                owner_session.begin(),
            ):
                owner_session.execute(text("SET LOCAL lock_timeout='2s'"))
                _try_neutral_lock(owner_session, f"submit:{owner}")
                with pytest.raises(DBAPIError) as caught:
                    lease.pulse(force=True)
                _owner_busy(caught.value)
            assert lease.lost.is_set() and lease.cancelled.is_set()
            assert lease.last_pulse == prior_success and lease.thread.ident is None
            _require_unchanged(source, before)
            assert env.rd_calls == env.oe_calls == []
    finally:
        source.dispose()
