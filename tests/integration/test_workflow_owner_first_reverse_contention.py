"""An independent neutral caller still fails closed behind an owner-first pulse."""

import os
from concurrent.futures import Future
from dataclasses import replace
from threading import Event, Thread, current_thread
from uuid import uuid4

import pytest
from rd_completed_graph_probe import _require_unchanged, _snapshot
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_lock_order import _run_attempt
from test_workflow_owner_first_heartbeat import _lease_times
from workflow_structured_stage_fixture import actual_environment, create_workflow

from tests.sql_probes import rd_native_admission_trial as trial

pytestmark = pytest.mark.postgres


def test_owner_first_heartbeat_preserves_independent_neutral_nowait_and_fence(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_TICKETED_NATIVE_TRIAL_REQUIRED") != "1":
        pytest.fail(
            "Explicit isolated owner-first scheduling proof required", pytrace=False
        )

    import test_backup_restore as legacy

    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from backend.app.research_lifecycle import (
        neutral_transaction,
        require_neutral_transaction,
    )
    from backend.app.services import DomainError
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease
    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "032g_schema_keyset_cost")
        storage = tmp_path / "heartbeat-owner-first-reverse-source"
        storage.mkdir(mode=0o700)
        with actual_environment(
            source, legacy._note_settings(source, storage), monkeypatch
        ) as env:
            workflow = create_workflow(env)
            owner = env.account.user.id
            assert (
                drive_one(
                    env.factory, env.settings, owner_id=owner, workflow_id=workflow
                ).state
                == "child_queued"
            )
            with env.factory() as session:
                run = session.scalars(
                    select(FactorWorkflowSubmission.run_id)
                    .filter_by(owner_id=owner, workflow_id=workflow)
                    .limit(2)
                ).one()

            worker = "heartbeat-owner-first-reverse-owned-fixture"
            # A separate factory represents an independent caller not covered by
            # the in-process FIFO. It uses production neutral_transaction and
            # the same installed graph and PostgreSQL locks.
            independent = sessionmaker(source, expire_on_commit=False)
            fifo_wait = Event()
            neutral_result = Future()
            neutral_thread = None
            original_gate = trial.TicketedAdmission

            def observe_trial_gate(*args, **kwargs):
                gate = original_gate(*args, **kwargs)
                wait = gate._condition.wait

                def observe_wait(*wait_args, **wait_kwargs):
                    if current_thread() is neutral_thread:
                        fifo_wait.set()
                    return wait(*wait_args, **wait_kwargs)

                # Observe the real candidate FIFO wait. Never replace the wait,
                # mutate its cutoff, or add a production scheduling hook.
                gate._condition.wait = observe_wait
                return gate

            monkeypatch.setattr(trial, "TicketedAdmission", observe_trial_gate)
            with trial.native_admission_trial() as counts:
                fence = rdagent_queue.claim_configured_run(
                    env.factory, run, 1, worker, env.settings
                )
                assert fence is not None and fence.owner_id == owner
                lease = AttemptLease(
                    env.factory,
                    env.settings,
                    fence.attempt_id,
                    fence.generation,
                    fence.lease_token,
                    worker,
                )
                acquired, release = Event(), Event()

                def observe_advisory(_conn, _cursor, statement, _params, _ctx, _many):
                    if (
                        current_thread() is lease.thread
                        and statement.lstrip().startswith(
                            "SELECT pg_advisory_xact_lock("
                        )
                    ):
                        acquired.set()
                        if not release.wait(15):
                            raise AssertionError("Heartbeat owner observation expired")

                def enter_registered_neutral():
                    try:
                        with neutral_transaction(env.factory, fence) as session:
                            locked_run, locked_attempt = require_neutral_transaction(
                                session, fence
                            )
                            assert lease.last_pulse > 0, (
                                "Neutral entered before pulse commit"
                            )
                            result = locked_run.id, locked_attempt.id
                        neutral_result.set_result(result)
                    except Exception as exc:  # noqa: BLE001 -- relay thread failure
                        neutral_result.set_exception(exc)

                prior = _lease_times(source, fence.attempt_id)
                event.listen(source, "after_cursor_execute", observe_advisory)
                try:
                    lease.start()
                    assert acquired.wait(10), (
                        "Patched heartbeat did not obtain owner lock"
                    )
                    # This is the actual original one-shot neutral path, not
                    # a raw pg_try query or a synthetic exception. With scope
                    # none it has exactly one candidate advisory key: submit:owner.
                    with (
                        pytest.raises(DomainError) as caught,
                        neutral_transaction(independent, fence, resource_scope="none"),
                    ):
                        pytest.fail(
                            "Independent neutral caller gained locked authority"
                        )
                    assert caught.value.code == "neutral_busy"
                    # Confirm neither failed neutral nor paused heartbeat holds
                    # the Run/Attempt row locks, and release this observer by rollback.
                    with Session(source) as inspect, inspect.begin():
                        _run_attempt(inspect, fence)
                        inspect.rollback()
                    assert _lease_times(source, fence.attempt_id) == prior
                    assert lease.last_pulse == 0
                    assert not lease.lost.is_set() and not lease.cancelled.is_set()
                    neutral_thread = Thread(
                        target=enter_registered_neutral,
                        name="owner-first-registered-neutral",
                        daemon=True,
                    )
                    neutral_thread.start()
                    assert fifo_wait.wait(5), (
                        "Registered neutral did not enter real FIFO wait"
                    )
                    assert not neutral_result.done() and lease.last_pulse == 0
                finally:
                    # Keep the actual maintenance thread registered with the
                    # trial for its join/fail-closed restoration on any failure.
                    lease.done.set()
                    release.set()
                    if lease.thread.ident is not None:
                        lease.thread.join(timeout=15)
                    event.remove(source, "after_cursor_execute", observe_advisory)
                    if neutral_thread is not None and neutral_thread.ident is not None:
                        neutral_thread.join(timeout=40)
                        assert not neutral_thread.is_alive(), (
                            "Registered neutral thread remained live"
                        )
                assert neutral_result.result(timeout=0) == (run, fence.attempt_id)
                assert not lease.thread.is_alive()
                assert not lease.lost.is_set() and not lease.cancelled.is_set()
                renewed = _lease_times(source, fence.attempt_id)
                assert all(new > old for new, old in zip(renewed, prior, strict=True))
                assert lease.last_pulse > 0 and counts["owner_first_pulses"] == 1

                # Normal renewal is the only intended mutation. Both factories
                # regain scoped authority after commit, and neither bypasses it.
                graph = _snapshot(source)
                with (
                    pytest.raises(DomainError) as caught,
                    neutral_transaction(
                        independent, replace(fence, lease_token=uuid4())
                    ),
                ):
                    pytest.fail("Wrong token retained neutral authority")
                assert caught.value.code == "attempt_fenced"
                with neutral_transaction(independent, fence) as session:
                    locked_run, locked_attempt = require_neutral_transaction(
                        session, fence
                    )
                    assert (locked_run.id, locked_attempt.id) == (run, fence.attempt_id)
                assert counts["neutral_commits"] == 1
                assert _lease_times(source, fence.attempt_id) == renewed
                _require_unchanged(source, graph)
            assert env.rd_calls == env.oe_calls == []
    finally:
        source.dispose()
