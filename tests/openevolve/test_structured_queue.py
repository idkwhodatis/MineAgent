"""Normal queued claim transaction boundaries; SQL/physical readers explicit doubles."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.services import DomainError
from tests.openevolve.test_structured_provider_submission import request
from workers.research import oe_structured_queue as queue


def arranged(monkeypatch):
    owner, run_id = uuid4(), uuid4()
    run = NS(
        id=run_id, owner_id=owner, status="queued", generation=1, active_attempt_id=None
    )
    req = request()
    config = NS(
        fixed_inputs="fixed",
        runtime="runtime",
        model_binding="binding",
        budget=req.budget,
    )
    state = NS(
        guard=False,
        commit=False,
        prior=None,
        can_submit=True,
        event_count=1,
        failed_commit=False,
    )
    added, calls = [], []

    class Session:
        def __enter__(self):
            self.values = iter((owner, run, state.prior))
            return self

        def __exit__(self, *a):
            return False

        def scalar(self, q):
            return next(self.values)

        def scalars(self, q):
            return NS(all=lambda: [NS(run_id=run_id, generation=1)] * state.event_count)

        def add(self, row):
            assert state.guard and not state.commit
            added.append(row)

        def flush(self):
            pass

        def commit(self):
            assert state.guard
            if state.failed_commit:
                raise DomainError("commit_failed", "test", 409)
            state.commit = True
            calls.append("claim_commit")

    session = Session()

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            return session

    @contextmanager
    def retained(*a):
        assert not state.guard
        state.guard = True
        try:
            yield object()
        finally:
            state.guard = False

    @contextmanager
    def neutral(*a, **kw):
        assert state.commit and not state.guard
        calls.append("budget_txn")
        yield NS(get=lambda *a: run)

    monkeypatch.setattr(queue, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(queue, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        queue, "require_dispatch_binding", lambda *a: (req, config, "a" * 64)
    )
    monkeypatch.setattr(queue, "retained_submission_data", retained)
    monkeypatch.setattr(
        queue,
        "evaluate",
        lambda *a: (
            NS(can_submit=state.can_submit, preflight_sha256="a" * 64),
            "fixed",
            NS(runtime="runtime"),
            "binding",
        ),
    )
    monkeypatch.setattr(queue, "neutral_transaction", neutral)
    monkeypatch.setattr(queue, "decode_spec", lambda *a: (None, config))
    monkeypatch.setattr(
        queue, "initialize_budget", lambda *a: calls.append("budget_verify")
    )

    def execute():
        return queue.claim_structured_run(
            Factory(), run_id, 1, "worker", NS(storage_root="/unused")
        )

    return NS(**locals())


def test_real_attempt_shape_commits_while_retained_inputs_guarded(monkeypatch):
    c = arranged(monkeypatch)
    fence = c.execute()
    assert len(c.added) == 1
    attempt = c.added[0]
    assert attempt.id == fence.attempt_id == c.run.active_attempt_id
    assert (attempt.lease_expires_at - attempt.heartbeat_at).total_seconds() == 720
    assert (
        attempt.sandbox_id == "not-launched:oe-structured"
        and not attempt.sandbox_stopped
    )
    assert c.calls == ["claim_commit", "budget_txn", "budget_verify"]


@pytest.mark.parametrize("reason", ["not_queued", "active", "prior"])
def test_duplicate_delivery_never_creates_second_attempt(monkeypatch, reason):
    c = arranged(monkeypatch)
    if reason == "not_queued":
        c.run.status = "running"
    elif reason == "active":
        c.run.active_attempt_id = uuid4()
    else:
        c.state.prior = uuid4()
    assert c.execute() is None
    assert not c.added and not c.calls


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_duplicate_outbox_refused(monkeypatch, count):
    c = arranged(monkeypatch)
    c.state.event_count = count
    with pytest.raises(DomainError):
        c.execute()
    assert not c.added


def test_fresh_preflight_drift_cannot_claim(monkeypatch):
    c = arranged(monkeypatch)
    c.state.can_submit = False
    with pytest.raises(DomainError):
        c.execute()
    assert not c.added


def test_commit_failure_never_initializes_budget_or_returns_fence(monkeypatch):
    c = arranged(monkeypatch)
    c.state.failed_commit = True
    with pytest.raises(DomainError):
        c.execute()
    assert not c.calls


@pytest.mark.parametrize("generation", [True, 0, 2])
def test_claim_rejects_other_generations(generation):
    with pytest.raises(DomainError):
        queue.claim_structured_run(None, uuid4(), generation, "worker", None)


@pytest.mark.parametrize(
    "drift", [None, "revision", "event", "receipt", "runtime", "binding"]
)
def test_dispatch_requires_exact_original_normal_receipt_and_current_profile(
    monkeypatch, drift
):
    from packages.factors.contracts import canonical_bytes

    req = request()
    owner, run_id, receipt_id = uuid4(), uuid4(), uuid4()
    run = NS(id=run_id, owner_id=owner, generation=1, idempotency_key="key")
    row = NS(
        id=receipt_id,
        owner_id=owner,
        run_id=run_id,
        idempotency_key="key",
        request_canonical=canonical_bytes(req),
        preflight_sha256="a" * 64,
    )
    config = NS(runtime="runtime", model_binding="binding")
    session = NS(
        scalars=lambda q: NS(
            all=lambda: [
                "026_rd_seeded_submissions" if drift == "revision" else queue.REVISION
            ]
        ),
        get=lambda *a: row,
    )
    event = NS(run_id=uuid4() if drift == "event" else run_id, generation=1)
    monkeypatch.setattr(
        queue, "decode_spec", lambda *a: (NS(submission_id=receipt_id), config)
    )
    monkeypatch.setattr(
        queue,
        "existing_receipt",
        lambda *a: None if drift == "receipt" else NS(id=receipt_id, run_id=run_id),
    )
    monkeypatch.setattr(
        queue,
        "resolve_runtime",
        lambda *a: NS(runtime="other" if drift == "runtime" else "runtime"),
    )
    monkeypatch.setattr(
        queue,
        "resolve_operator_profile",
        lambda *a: (None, "other" if drift == "binding" else "binding"),
    )
    if drift is None:
        actual, _, sha = queue.require_dispatch_binding(session, run, event, NS())
        assert actual == req and sha == "a" * 64
    else:
        with pytest.raises(DomainError):
            queue.require_dispatch_binding(session, run, event, NS())
