"""Isolated 032g PostgreSQL contention proof for the test-only owner-first pulse."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_lock_order import _run_attempt
from workflow_structured_stage_fixture import actual_environment, create_workflow

from tests.sql_probes.rd_native_admission_trial import native_admission_trial

pytestmark = pytest.mark.postgres

_BLOCKED_BY_HOLDER = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM pg_locks AS waiting
        WHERE waiting.locktype = 'advisory'
          AND NOT waiting.granted
          AND waiting.database = (
              SELECT oid FROM pg_database WHERE datname = current_database()
          )
          AND pg_backend_pid() = ANY(pg_blocking_pids(waiting.pid))
    )
    """
)


@contextmanager
def _owner_held_by_independent_session(
    engine, fence, *, release_on_wait
) -> Iterator[None]:
    from backend.app.research_lifecycle import _try_neutral_lock

    held, pulse_returned = Event(), Event()
    result = Queue(maxsize=1)

    def hold():
        try:
            with Session(engine) as owner_session, owner_session.begin():
                # Bounds apply only to the independent test observer, not the
                # pulse, installed graph guards, or controller.
                owner_session.execute(text("SET LOCAL statement_timeout = '1s'"))
                _try_neutral_lock(owner_session, f"submit:{fence.owner_id}")
                held.set()
                stop = monotonic() + 3
                while not owner_session.scalar(_BLOCKED_BY_HOLDER):
                    remaining = stop - monotonic()
                    if remaining <= 0 or pulse_returned.wait(min(0.02, remaining)):
                        raise AssertionError(
                            "No actual PostgreSQL advisory wait from the heartbeat"
                        )
                # The heartbeat holds no Run/Attempt row locks while waiting:
                # this is PostgreSQL NOWAIT, not an ordering-only mock.
                _run_attempt(owner_session, fence)
                if not release_on_wait and not pulse_returned.wait(8):
                    raise AssertionError(
                        "Heartbeat did not finish under held owner lock"
                    )
            result.put_nowait(None)
        except Exception as exc:  # noqa: BLE001 -- relay holder failures to the test thread
            result.put_nowait(exc)
        finally:
            held.set()

    holder = Thread(target=hold, name="owner-first-pg-holder", daemon=True)
    holder.start()
    try:
        assert held.wait(15), "PostgreSQL owner fixture did not become ready"
        if not result.empty():
            raise AssertionError("PostgreSQL owner fixture exited before pulse")
        yield
    finally:
        pulse_returned.set()
        holder.join(timeout=15)
        assert not holder.is_alive(), "PostgreSQL owner fixture did not close"
        try:
            failure = result.get_nowait()
        except Empty:
            pytest.fail(
                "PostgreSQL owner fixture did not report its result", pytrace=False
            )
        if failure is not None:
            raise AssertionError(
                "Independent PostgreSQL owner observation failed"
            ) from failure


def _lease_times(engine, attempt_id):
    from backend.app.models import ExecutionAttempt

    with Session(engine) as session:
        return tuple(
            session.execute(
                select(
                    ExecutionAttempt.heartbeat_at, ExecutionAttempt.lease_expires_at
                ).where(ExecutionAttempt.id == attempt_id)
            ).one()
        )


def test_actual_owner_first_pulse_waits_before_row_locks_and_times_out_closed(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_TICKETED_NATIVE_TRIAL_REQUIRED") != "1":
        pytest.fail(
            "Explicit isolated owner-first scheduling proof required", pytrace=False
        )

    import test_backup_restore as legacy
    from rd_completed_graph_probe import _require_unchanged, _snapshot

    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease
    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "032g_schema_keyset_cost")
        storage = tmp_path / "heartbeat-owner-first-source"
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

            worker = "heartbeat-owner-first-owned-fixture"
            with native_admission_trial() as counts:
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
                before = _lease_times(source, fence.attempt_id)
                # Main thread runs the actual patched pulse with its original
                # transaction/commit; only the holder is a dedicated thread.
                with _owner_held_by_independent_session(
                    source, fence, release_on_wait=True
                ):
                    assert lease.pulse(force=True) is None
                after = _lease_times(source, fence.attempt_id)
                assert all(new > old for new, old in zip(after, before, strict=True))
                assert lease.last_pulse > 0 and counts["owner_first_pulses"] == 1
                assert not lease.lost.is_set() and not lease.cancelled.is_set()

                graph = _snapshot(source)
                previous = lease.last_pulse
                with _owner_held_by_independent_session(
                    source, fence, release_on_wait=False
                ):
                    with pytest.raises(DBAPIError) as caught:
                        lease.pulse(force=True)
                    assert caught.value.orig.sqlstate == "55P03"
                assert lease.lost.is_set() and lease.cancelled.is_set()
                assert lease.last_pulse == previous
                assert counts["owner_first_pulses"] == 1
                assert _lease_times(source, fence.attempt_id) == after
                _require_unchanged(source, graph)
                assert lease.thread.ident is None
            assert env.rd_calls == env.oe_calls == []
    finally:
        source.dispose()
