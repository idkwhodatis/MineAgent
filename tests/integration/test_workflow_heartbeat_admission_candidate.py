"""Isolated local-gate candidate on real031/032g workflow metadata only.

No runtime hook, queue, controller or SDK is activated. Original neutral and
pulse transactions (including commit) are explicitly enclosed by one candidate
in two test threads. External contention uses a separate spawned process with
its own engine. This does not prove native callback coverage or performance.
"""

import multiprocessing
import os
from contextlib import contextmanager
from threading import Event, Thread, current_thread
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rd_completed_graph_probe import _require_unchanged, _snapshot
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_lock_order import _owner_busy, _run_attempt
from workflow_structured_stage_fixture import actual_environment, create_workflow

from backend.app.models import utcnow
from backend.app.research_lifecycle import _try_neutral_lock, neutral_transaction
from backend.app.services import DomainError
from tests.sql_probes.rd_attempt_admission_candidate import AttemptAdmission
from workers.research.lease import AttemptLease

pytestmark = pytest.mark.postgres


@pytest.fixture(
    params=["031_workflow_structured_stage", "032g_schema_keyset_cost"],
    ids=["031", "032g"],
)
def candidate_workflow(drill_databases, tmp_path, monkeypatch, request):
    if os.environ.get("MINEAGENT_HEARTBEAT_ADMISSION_CANDIDATE_REQUIRED") != "1":
        pytest.fail("Explicit isolated admission candidate required", pytrace=False)
    import test_backup_restore as legacy

    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from workers.research.rdagent_queue import claim_configured_run
    from workers.research.workflow_tick import drive_one

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, request.param)
        storage = tmp_path / "heartbeat-admission-source"
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
                env.factory, runs[0], 1, "admission-candidate-fixture", env.settings
            )
            assert fence is not None
            # The clocks precede the read; reading does not refill the lease.
            started, wall = monotonic(), utcnow()
            with source.connect() as c, c.begin():
                c.exec_driver_sql("SET TRANSACTION READ ONLY")
                row = c.execute(
                    text("""SELECT a.lease_expires_at
                  FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id
                  WHERE a.id=:attempt AND a.run_id=:run AND a.generation=:generation
                  AND a.lease_token=:token AND r.active_attempt_id=a.id
                  AND r.generation=a.generation AND r.owner_id=:owner
                  AND r.status='running' AND a.status='running'
                  AND r.config->>'schema_version'='mineagent.factor-research.v3'"""),
                    {
                        "attempt": fence.attempt_id,
                        "run": fence.run_id,
                        "generation": fence.generation,
                        "token": fence.lease_token,
                        "owner": fence.owner_id,
                    },
                ).scalar_one()
                assert c.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all() == [(request.param,)]
            gate = AttemptAdmission(
                env.factory,
                fence,
                lease_expires_at=row,
                observation_started_utc=wall,
                observation_started_monotonic=started,
            )
            lease = AttemptLease(
                env.factory,
                env.settings,
                fence.attempt_id,
                fence.generation,
                fence.lease_token,
                "admission-candidate-fixture",
            )
            state = SimpleNamespace(
                env=env,
                source=source,
                fence=fence,
                gate=gate,
                lease=lease,
                database_url=drill_databases.source,
            )
            try:
                yield state
                assert env.rd_calls == env.oe_calls == []
                assert lease.thread.ident is None
                with source.connect() as c:
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
            finally:
                gate.close()
    finally:
        source.dispose()


def _operation(state, role, on_enter=lambda: None):
    with state.gate.transaction(state.env.factory, state.fence, role=role):
        on_enter()
        if role == "heartbeat":
            assert state.lease.pulse(force=True) is None
        else:
            with neutral_transaction(state.env.factory, state.fence) as session:
                assert session.execute(text("SELECT 1")).scalar_one() == 1


def _launch(function, name):
    errors = []

    def run():
        try:
            function()
        except BaseException as error:  # noqa: BLE001 -- retain privately for caller
            errors.append(error)

    thread = Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread, errors


def _join(thread):
    thread.join(timeout=40)
    if thread.is_alive():
        raise AssertionError("Owned candidate transaction thread did not finish")


def _no_errors(errors):
    if errors:
        from rd_rpc_diagnostic import _queue_failure

        # Never render exception details, SQL parameters or fence identities.
        raise AssertionError(
            "Candidate transaction failed: " + str(_queue_failure(errors[0]))
        ) from None


def _require_fenced_failure(errors):
    if (
        len(errors) != 1
        or not isinstance(errors[0], DomainError)
        or type(errors[0].code) is not str
        or errors[0].code != "attempt_fenced"
    ):
        raise AssertionError("Expected one original live-fence rejection") from None


def _observe_wait(monkeypatch, gate, thread_name):
    waiting = Event()
    original = gate._condition.wait

    def wait(*, timeout):
        if current_thread().name == thread_name:
            waiting.set()
        return original(timeout=timeout)

    monkeypatch.setattr(gate._condition, "wait", wait)
    return waiting


@pytest.mark.parametrize("first_role", ["neutral", "heartbeat"])
def test_candidate_serializes_original_transactions_through_commit(
    candidate_workflow, monkeypatch, first_role
):
    state = candidate_workflow
    before = _snapshot(state.source)
    with state.source.connect() as c:
        attempt_before = c.execute(
            text(
                "SELECT to_jsonb(a)-'heartbeat_at'-'lease_expires_at' FROM execution_attempts a WHERE id=:id"
            ),
            {"id": state.fence.attempt_id},
        ).scalar_one()
    cutoff = state.gate._cutoff
    paused, release, committed = Event(), Event(), Event()
    waiting = _observe_wait(monkeypatch, state.gate, "candidate-second")

    def before_commit(_session):
        if current_thread().name == "candidate-first" and not paused.is_set():
            paused.set()
            if not release.wait(20):
                raise AssertionError("Candidate commit observation timed out")

    def after_commit(_session):
        if current_thread().name == "candidate-first":
            committed.set()

    def second_enter():
        if not committed.is_set():
            raise AssertionError("Candidate released before original commit")

    event.listen(Session, "before_commit", before_commit)
    event.listen(Session, "after_commit", after_commit)
    threads = []
    try:
        first = _launch(lambda: _operation(state, first_role), "candidate-first")
        threads.append(first)
        assert paused.wait(20)
        other = "heartbeat" if first_role == "neutral" else "neutral"
        second = _launch(
            lambda: _operation(state, other, second_enter), "candidate-second"
        )
        threads.append(second)
        assert waiting.wait(5)
        release.set()
        for thread, errors in threads:
            _join(thread)
            _no_errors(errors)
    finally:
        release.set()
        state.gate.close()
        try:
            for thread, _ in threads:
                _join(thread)
        finally:
            event.remove(Session, "before_commit", before_commit)
            event.remove(Session, "after_commit", after_commit)
    assert committed.is_set()
    assert state.lease.last_pulse > 0
    assert not state.lease.lost.is_set() and not state.lease.cancelled.is_set()
    assert state.gate._cutoff == cutoff
    after = _snapshot(state.source)
    if after[0] != before[0]:
        raise AssertionError("Candidate altered installed catalog")
    changed = {table for table in before[1] if before[1][table] != after[1][table]}
    assert changed == {"execution_attempts", "worker_heartbeats"}
    with state.source.connect() as c:
        attempt_after = c.execute(
            text(
                "SELECT to_jsonb(a)-'heartbeat_at'-'lease_expires_at' FROM execution_attempts a WHERE id=:id"
            ),
            {"id": state.fence.attempt_id},
        ).scalar_one()
    if attempt_after != attempt_before:
        raise AssertionError("Candidate changed non-heartbeat attempt fields")


def _external_owner(database_url, owner_id, channel):
    """Owned spawned process; IPC contains only fixed readiness/release flags."""
    import test_backup_restore as legacy

    source = None
    try:
        if not legacy.DATABASE_NAME.fullmatch(database_url.database or ""):
            raise ValueError("Owned fixture database required")
        source = legacy._engine(database_url)
        with Session(source) as session, session.begin():
            session.execute(text("SET LOCAL lock_timeout='2s'"))
            _try_neutral_lock(session, f"submit:{owner_id}")
            channel.send_bytes(b"ready")
            if not channel.poll(60) or channel.recv_bytes(16) != b"release":
                raise ValueError("Bounded owner release required")
        channel.send_bytes(b"released")
    except BaseException:  # noqa: BLE001 -- no credentials or raw exceptions in child
        try:
            channel.send_bytes(b"failed")
        except BaseException:  # noqa: BLE001, S110 -- parent checks process/IPC outcome
            pass
    finally:
        if source is not None:
            source.dispose()
        channel.close()


@contextmanager
def _held_external_owner(state):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_external_owner,
        args=(state.database_url, state.fence.owner_id, child),
        daemon=True,
    )
    started = ready = False
    try:
        process.start()
        started = True
        child.close()
        assert parent.poll(20) and parent.recv_bytes(16) == b"ready"
        ready = True
        yield
    finally:
        released = forced = alive = False
        exitcode = None
        try:
            if started:
                if ready:
                    try:
                        parent.send_bytes(b"release")
                    except (OSError, EOFError):
                        ready = False
                process.join(timeout=5)
                forced = process.is_alive()
                if forced:
                    process.terminate()  # Only this exact context-owned child.
                    process.join(timeout=5)
                if process.is_alive():
                    process.kill()  # Exact-child fallback, never a name/PID scan.
                    process.join(timeout=5)
                alive, exitcode = process.is_alive(), process.exitcode
                if not alive:
                    process.close()
                if ready:
                    try:
                        released = (
                            parent.poll(1) and parent.recv_bytes(16) == b"released"
                        )
                    except (OSError, EOFError):
                        released = False
        finally:
            parent.close()
            child.close()
        if started:
            assert not forced and not alive and exitcode == 0 and released


@pytest.mark.parametrize("role", ["neutral", "heartbeat"])
def test_candidate_preserves_external_process_contention_failure(
    candidate_workflow, role
):
    state = candidate_workflow
    before = _snapshot(state.source)
    with _held_external_owner(state):
        if role == "heartbeat":
            with pytest.raises(DBAPIError) as caught:
                _operation(state, role)
            _owner_busy(caught.value)
            assert state.lease.lost.is_set() and state.lease.cancelled.is_set()
            assert state.lease.last_pulse == 0
        else:
            with pytest.raises(DomainError) as caught:
                _operation(state, role)
            assert caught.value.code == "neutral_busy"
    _require_unchanged(state.source, before)


@pytest.mark.parametrize("role", ["neutral", "heartbeat"])
@pytest.mark.parametrize("change", ["expiry", "token"])
def test_candidate_wait_never_replaces_live_fence(
    candidate_workflow, monkeypatch, role, change
):
    state = candidate_workflow
    waiting = _observe_wait(monkeypatch, state.gate, "candidate-stale")
    cutoff = state.gate._cutoff
    thread = None
    try:
        with state.gate.transaction(state.env.factory, state.fence, role="neutral"):
            thread, errors = _launch(lambda: _operation(state, role), "candidate-stale")
            assert waiting.wait(5)
            # Adversarial fixture changes use every original SQL guard. Failure
            # to commit is a red probe, never permission to bypass constraints.
            with state.env.factory.begin() as session:
                _try_neutral_lock(session, f"submit:{state.fence.owner_id}")
                _run_attempt(session, state.fence)
                if change == "token":
                    session.execute(
                        text(
                            "UPDATE execution_attempts SET lease_token=:token WHERE id=:id"
                        ),
                        {"token": uuid4(), "id": state.fence.attempt_id},
                    )
                else:
                    session.execute(
                        text(
                            "UPDATE execution_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                        ),
                        {"id": state.fence.attempt_id},
                    )
            changed = _snapshot(state.source)
        _join(thread)
        _require_fenced_failure(errors)
        if role == "heartbeat":
            assert state.lease.lost.is_set() and state.lease.cancelled.is_set()
            assert state.lease.last_pulse == 0
        _require_unchanged(state.source, changed)
        assert state.gate._cutoff == cutoff
    finally:
        state.gate.close()
        if thread is not None:
            _join(thread)
