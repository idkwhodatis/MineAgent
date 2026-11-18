"""Offline lifecycle contracts, not live native/SQL proof."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes import reverse_heartbeat_native_trial as m


def fake_trial(monkeypatch, *, start_error=None, alive=False):
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease

    calls = []
    connection = NS(exec_driver_sql=lambda sql: calls.append("sql"))

    @contextmanager
    def begin():
        yield connection

    engine = NS(url="owned", begin=begin, dispose=lambda: calls.append("dispose"))
    factory = NS(kw={"bind": engine})
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())

    @contextmanager
    def environment(*args):
        yield NS(factory=factory)
        calls.append("environment_exit")

    def original_start(lease):
        calls.append("start")
        if start_error is not None:
            raise start_error
        return "original-result"

    monkeypatch.setattr(AttemptLease, "start", original_start)
    monkeypatch.setattr(rdagent_queue, "claim_configured_run", lambda *a: fence)
    monkeypatch.setattr(m, "require_single_thread", lambda: None)
    monkeypatch.setattr(m, "require_native_absence", lambda: None)
    monkeypatch.setattr(m, "require_only_connection", lambda c: None)
    monkeypatch.setattr(m.candidate, "check", lambda *a, **k: None)
    monkeypatch.setattr(m, "restore_quiescent", lambda *a: calls.append("restore"))
    baseline = NS(actual_environment=environment)
    legacy = NS(_upgrade=lambda *a: calls.append("upgrade"))
    lease = NS(
        factory=factory,
        attempt_id=fence.attempt_id,
        generation=1,
        token=fence.lease_token,
        thread=NS(is_alive=lambda: alive),
    )
    return NS(
        calls=calls,
        engine=engine,
        baseline=baseline,
        legacy=legacy,
        lease=lease,
        factory=factory,
        attempt_class=AttemptLease,
        queue=rdagent_queue,
    )


def run_environment(t, monkeypatch):
    return t.baseline.actual_environment(
        t.engine, NS(storage_root="/owned-storage"), monkeypatch
    )


def test_trial_original_callbacks_and_upgrade_before_native(monkeypatch):
    t = fake_trial(monkeypatch)
    with m.isolated_native_sql_trial(
        t.baseline, t.legacy, "owned", monkeypatch
    ) as state:
        t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
        with run_environment(t, monkeypatch):
            t.queue.claim_configured_run(t.factory)
            assert t.attempt_class.start(t.lease) == "original-result"
    assert state["starts"] == 1 and state["restored"]
    assert t.calls == [
        "upgrade",
        "sql",
        "start",
        "environment_exit",
        "dispose",
        "restore",
        "dispose",
    ]


def test_trial_tracks_launch_error_and_does_not_restore_live_thread(monkeypatch):
    failure = RuntimeError("private-worker-error")
    t = fake_trial(monkeypatch, start_error=failure, alive=True)
    with pytest.raises(AssertionError, match="thread remains live"):  # noqa: SIM117 -- exception boundary
        with m.isolated_native_sql_trial(t.baseline, t.legacy, "owned", monkeypatch):
            t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
            with run_environment(t, monkeypatch):
                t.queue.claim_configured_run(t.factory)
                with pytest.raises(RuntimeError) as caught:
                    t.attempt_class.start(t.lease)
                assert caught.value is failure
    assert "restore" not in t.calls and "dispose" not in t.calls


def test_trial_original_body_failure_survives_successful_restore(monkeypatch):
    t = fake_trial(monkeypatch)
    failure = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught:  # noqa: SIM117 -- exception boundary
        with m.isolated_native_sql_trial(t.baseline, t.legacy, "owned", monkeypatch):
            t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
            with run_environment(t, monkeypatch):
                raise failure
    assert caught.value is failure
    assert t.calls[-2:] == ["restore", "dispose"]


def test_trial_does_not_restore_without_environment_closure(monkeypatch):
    t = fake_trial(monkeypatch)
    with pytest.raises(AssertionError, match="closure unconfirmed"):  # noqa: SIM117 -- exception boundary
        with m.isolated_native_sql_trial(t.baseline, t.legacy, "owned", monkeypatch):
            t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
    assert "restore" not in t.calls


def test_trial_rejects_wrong_claim_before_original_start(monkeypatch):
    t = fake_trial(monkeypatch)
    with m.isolated_native_sql_trial(t.baseline, t.legacy, "owned", monkeypatch):
        t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
        with run_environment(t, monkeypatch):  # noqa: SIM117 -- exception boundary
            with pytest.raises(AssertionError, match="claim binding"):
                t.attempt_class.start(t.lease)
    assert "start" not in t.calls


def test_native_absence_checks_all_exact_categories_and_no_kill(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return NS(returncode=0, stdout=b"")

    monkeypatch.setattr(m.subprocess, "run", run)
    m.require_native_absence()
    assert len(calls) == 6
    assert tuple(args[-1] for args, _ in calls) == m.FILTERS
    assert all(
        args[:5] == ["docker", "ps", "--all", "--quiet", "--no-trunc"]
        for args, _ in calls
    )
    assert all(
        options["timeout"] == 15 and options["capture_output"] for _, options in calls
    )
    monkeypatch.setattr(
        m.subprocess, "run", lambda *a, **k: NS(returncode=0, stdout=b"private-id")
    )
    with pytest.raises(AssertionError, match="unconfirmed") as caught:
        m.require_native_absence()
    assert "private-id" not in str(caught.value)


def test_session_and_thread_uncertainty_refuse_restore(monkeypatch):
    connection = NS(execute=lambda _: NS(scalar_one=lambda: 1))
    with pytest.raises(AssertionError, match="other database clients"):
        m.require_only_connection(connection)
    monkeypatch.setattr(m.threading, "enumerate", lambda: [NS(is_alive=lambda: True)])
    with pytest.raises(AssertionError, match="closed local clients"):
        m.require_single_thread()


def test_native_trial_source_preserves_original_execution_and_commit_barriers():
    helper = Path(m.__file__).read_text()
    assert "transaction.commit()" in helper
    assert helper.index("with storage_guard(") < helper.index("transaction.commit()")
    assert helper.index("_lock(connection, exclusive=True)") < helper.index(
        "with storage_guard("
    )
    assert "candidate.installed_candidate" not in helper
    assert "pg_terminate_backend" not in helper
    assert "AttemptLease.pulse" not in helper
    assert "AttemptLease.close" not in helper
    source = Path(
        "tests/integration/test_workflow_reverse_heartbeat_native_trial.py"
    ).read_text()
    assert (
        "baseline.test_actual_current032g_workflow_completes_with_readonly_graph_checks("
        in source
    )
    assert "native_admission_trial" not in source
    assert "MINEAGENT_REVERSE_HEARTBEAT_NATIVE_REQUIRED" in source


def test_real_queue_separate_engine_factory_is_exactly_claim_bound(monkeypatch):
    t = fake_trial(monkeypatch)
    worker_factory = NS(kw={"bind": NS(url="owned")})
    t.lease.factory = worker_factory
    with m.isolated_native_sql_trial(
        t.baseline, t.legacy, "owned", monkeypatch
    ) as state:
        t.legacy._upgrade(t.engine, "032g_schema_keyset_cost")
        with run_environment(t, monkeypatch):
            t.queue.claim_configured_run(worker_factory)
            assert t.attempt_class.start(t.lease) == "original-result"
    assert state["starts"] == 1 and state["restored"]


@pytest.mark.parametrize("failure", [None, "root", "binding", "control", "validator"])
def test_restored_original_workflow_all_roots_and_children(monkeypatch, failure):
    queries = []
    rows = [
        NS(id="oe", engine="openevolve", status="succeeded"),
        NS(id="rd", engine="rdagent", status="succeeded"),
    ]
    roots = [
        NS(id=status, owner_id="owner", status=status)
        for status in ("cancelled", "completed", "paused")
    ]
    stages = [
        NS(
            owner_id="owner",
            workflow_id="completed",
            kind="rd_factor_generation",
            child_run_id="rd",
        ),
        NS(
            owner_id="owner",
            workflow_id="completed",
            kind="structured_factor_execution",
            child_run_id="oe",
        ),
    ]
    if failure == "root":
        roots.pop()
    if failure == "binding":
        stages[0].workflow_id = "paused"

    class Connection:
        def exec_driver_sql(self, sql):
            queries.append(sql)
            values = (
                roots
                if "FROM research_workflows" in sql
                else stages
                if "FROM research_workflow_stages" in sql
                else rows
            )
            return NS(all=lambda: values)

        def execute(self, sql, parameters):
            sql = str(sql)
            queries.append(sql)
            if "research_workflow_commands" in sql:
                action = (
                    "cancel"
                    if parameters["wid"] == "cancelled"
                    else "pause_after_stage"
                )
                return NS(
                    scalars=lambda: NS(
                        all=lambda: ["bad"] if failure == "control" else [action]
                    )
                )
            return NS(scalar_one=lambda: failure != "validator")

        @contextmanager
        def begin(self):
            yield

    @contextmanager
    def connect():
        yield Connection()

    engine = NS(connect=connect)
    if failure:
        with pytest.raises(AssertionError):
            m.verify_restored_completed_graph(engine)
    else:
        m.verify_restored_completed_graph(engine)
        assert sum("mineagent_workflow_consistent" in sql for sql in queries) == 3
        assert sum("mineagent_rd_provider_graph" in sql for sql in queries) == 1
        assert sum("mineagent_oes_graph_check" in sql for sql in queries) == 1
    assert queries[0] == "SET TRANSACTION READ ONLY"
