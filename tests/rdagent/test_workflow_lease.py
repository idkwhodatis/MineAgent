from contextlib import contextmanager
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.app.services import FenceLost
from packages.research_runtime.fences import AttemptFence
from workers.research import lease as common
from workers.research import rdagent_workflow_lease as workflow


@pytest.fixture
def fence():
    return AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())


class Factory:
    def __init__(self, *, seconds=8, original_ms=1250):
        self.seconds = seconds
        self.original_ms = original_ms
        self.trace = []
        self.error = None

    @contextmanager
    def __call__(self):
        result = Mock()

        def observe(query, params):
            self.trace.append(("read", query, params))
            if self.error:
                raise self.error
            return SimpleNamespace(scalar_one_or_none=lambda: self.seconds)

        result.execute.side_effect = observe
        result.connection.side_effect = lambda **kwargs: self.trace.append(
            ("connection", kwargs)
        )
        yield result

    @contextmanager
    def begin(self):
        def execute(query, params=None):
            self.trace.append(("write", query, params))
            if query is workflow._TIMEOUT:
                return SimpleNamespace(one=lambda: ("1.25s", self.original_ms))
            return None

        self.trace.append(("begin",))
        try:
            yield SimpleNamespace(execute=execute)
        except BaseException:
            self.trace.append(("rollback",))
            raise
        else:
            self.trace.append(("commit",))


@pytest.mark.parametrize(
    ("mode", "lease_cls"),
    [(True, workflow.WorkflowAttemptLease), (False, common.AttemptLease)],
)
def test_committed_mode_selects_lease_without_another_read(fence, mode, lease_cls):
    factory = Factory()
    instance = workflow.configured_rd_lease(
        factory, None, fence, "worker", workflow=mode
    )
    assert type(instance) is lease_cls
    assert not factory.trace


@pytest.mark.parametrize(
    ("schema", "commits", "expected"),
    [
        ("mineagent.factor-research.v3", True, [True]),
        ("mineagent.factor-research.v3", False, []),
        ("mineagent.factor-research.v2", True, [False]),
        ("mineagent.factor-research.v1", True, [False]),
    ],
)
def test_claim_reports_verified_mode_only_after_commit(
    monkeypatch, fence, schema, commits, expected
):
    from workers.research import rdagent_queue as queue

    class Refused(Exception):
        pass

    factory = Factory()
    mode = []
    monkeypatch.setattr(queue, "_claim", Mock(return_value=fence))
    monkeypatch.setattr(
        queue,
        "_queued_identity",
        Mock(return_value=SimpleNamespace(schema_version=schema, budget=object())),
    )
    monkeypatch.setattr(queue, "initialize_budget", Mock())
    monkeypatch.setattr(queue, "bind_research_attempt", Mock())

    @contextmanager
    def neutral(*args, **kwargs):
        assert not mode
        yield SimpleNamespace(get=Mock())
        assert not mode
        if not commits:
            raise Refused()

    monkeypatch.setattr(queue, "neutral_transaction", neutral)
    if not commits:
        with pytest.raises(Refused):
            queue.claim_configured_run(
                factory, fence.run_id, 1, "worker", None, _on_committed=mode.append
            )
    else:
        actual = queue.claim_configured_run(
            factory, fence.run_id, 1, "worker", None, _on_committed=mode.append
        )
        assert actual is fence
    assert mode == expected


@pytest.mark.parametrize(
    ("seconds", "original_ms", "bound"),
    [
        (8, 1250, "1250ms"),
        (8, 0, "4000ms"),
        (2, 1250, "1000ms"),
    ],
)
def test_owner_before_live_heartbeat_resets_bound_and_commits(
    monkeypatch, fence, seconds, original_ms, bound
):
    factory = Factory(seconds=seconds, original_ms=original_ms)
    instance = workflow.WorkflowAttemptLease(factory, None, fence, "worker")
    monkeypatch.setattr(workflow, "monotonic", Mock(side_effect=(10, 11, 11.5)))
    monkeypatch.setattr(
        workflow,
        "advisory_lock",
        lambda session, key: factory.trace.append(("owner", key)),
    )
    monkeypatch.setattr(
        common,
        "heartbeat",
        lambda *_: factory.trace.append(("live_fence",)) or True,
    )

    instance.pulse(force=True)

    assert factory.trace[1][:2] == ("read", workflow._OBSERVE)
    assert factory.trace[2:8] == [
        ("begin",),
        ("write", workflow._TIMEOUT, None),
        ("write", workflow._SET_TIMEOUT, {"timeout": bound}),
        ("owner", f"submit:{fence.owner_id}"),
        ("write", workflow._SET_TIMEOUT, {"timeout": "1.25s"}),
        ("live_fence",),
    ]
    assert factory.trace[-1] == ("commit",)
    assert instance.cancelled.is_set() and not instance.lost.is_set()


@pytest.mark.parametrize(
    ("seconds", "times", "witness"),
    [
        (None, (10,), "read"),
        (0, (10,), "read"),
        (0.5, (10, 11), "rollback"),
        (3, (10, 11, 13), "rollback"),
    ],
)
def test_missing_or_expired_ceiling_aborts_before_renewal(
    monkeypatch, fence, seconds, times, witness
):
    factory = Factory(seconds=seconds)
    instance = workflow.WorkflowAttemptLease(factory, None, fence, "worker")
    monkeypatch.setattr(workflow, "monotonic", Mock(side_effect=times))
    monkeypatch.setattr(workflow, "advisory_lock", Mock())
    heartbeat = Mock()
    monkeypatch.setattr(common, "heartbeat", heartbeat)

    with pytest.raises(FenceLost):
        instance.pulse(force=True)

    heartbeat.assert_not_called()
    assert factory.trace[-1][0] == witness
    assert (
        instance.last_pulse == 0
        and instance.lost.is_set()
        and instance.cancelled.is_set()
    )


def test_failed_identity_probe_never_leaks_bound_fence(fence):
    factory = Factory()
    factory.error = SQLAlchemyError(str(fence.lease_token))
    with pytest.raises(FenceLost) as caught:
        workflow.configured_rd_lease(
            factory, None, fence, "worker", workflow=True
        ).pulse(force=True)
    assert str(fence.lease_token) not in str(caught.value)
    assert caught.value.__suppress_context__


def test_plain_lease_retains_original_transaction(monkeypatch, fence):
    factory = Factory()
    lease = common.AttemptLease(
        factory, None, fence.attempt_id, 1, fence.lease_token, "worker"
    )
    monkeypatch.setattr(common, "heartbeat", lambda *_: False)
    lease.pulse(force=True)
    assert [x[0] for x in factory.trace] == ["begin", "write", "commit"]


def test_advisory_timeout_rolls_back_without_committing_authority(monkeypatch, fence):
    factory = Factory()
    instance = workflow.WorkflowAttemptLease(factory, None, fence, "worker")
    monkeypatch.setattr(workflow, "monotonic", Mock(side_effect=(10, 11)))
    timeout = SQLAlchemyError("lock timeout")
    monkeypatch.setattr(workflow, "advisory_lock", Mock(side_effect=timeout))
    heartbeat = Mock()
    monkeypatch.setattr(common, "heartbeat", heartbeat)
    with pytest.raises(SQLAlchemyError) as caught:
        instance.pulse(force=True)
    assert caught.value is timeout
    assert factory.trace[-1] == ("rollback",)
    heartbeat.assert_not_called()
    assert instance.last_pulse == 0
    assert instance.lost.is_set() and instance.cancelled.is_set()


def test_close_while_heartbeat_is_waiting_never_starts_write(monkeypatch, fence):
    from backend.app import rdagent_workflow_coordination as coord

    factory = Factory()
    instance = workflow.WorkflowAttemptLease(factory, None, fence, "worker")
    parked = Event()

    def pulse():
        with pytest.raises(FenceLost):
            instance.pulse(force=True)

    worker = Thread(target=pulse, daemon=True)
    with coord.registered(factory, fence) as gate:
        wait = gate._condition.wait

        def observe_wait(timeout):
            parked.set()
            return wait(timeout)

        monkeypatch.setattr(gate._condition, "wait", observe_wait)
        try:
            with coord.neutral(factory, fence):
                worker.start()
                assert parked.wait(2)
                # This direct-pulse test owns its thread, not AttemptLease's.
                instance.close()
        finally:
            worker.join(timeout=3)
    assert not worker.is_alive()
    assert instance.lost.is_set()
    assert instance.cancelled.is_set()
    assert [entry[0] for entry in factory.trace] == ["connection", "read"]
