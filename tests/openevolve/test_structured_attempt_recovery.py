"""Actual coordinator with transaction/physical boundary doubles, not PG/Docker."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from packages.research_runtime.process_contracts import ContainmentSummary, ProcessError
from workers.research import oe_structured_recovery as module


@pytest.fixture
def recovery(monkeypatch):
    run = NS(id=uuid4(), owner_id=uuid4(), status="cancel_requested", generation=1)
    attempt = NS(
        id=uuid4(),
        lease_token=uuid4(),
        lease_expires_at=utcnow() + timedelta(seconds=10),
        generation=1,
        sandbox_stopped=False,
        finished_at=None,
    )
    r = NS(
        run=run,
        attempt=attempt,
        revision=module.REVISION,
        rows=[],
        trace=[],
        events=[],
        sdk=True,
        confirmed=True,
        active=False,
        guard=False,
    )

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        @contextmanager
        def begin(self):
            assert not r.active
            r.active = True
            r.trace.append("begin")
            try:
                yield NS(
                    scalars=lambda q: NS(
                        all=lambda: (
                            [r.revision] if "alembic_version" in str(q) else r.rows
                        )
                    )
                )
            finally:
                r.active = False
            r.trace.append("commit")

    @contextmanager
    def lifecycle(*a):
        yield

    @contextmanager
    def guard(*a):
        assert not r.guard
        r.guard = True
        try:
            yield
        finally:
            r.guard = False

    def recover(*a):
        assert not r.active and not r.guard and r.trace[:2] == ["begin", "commit"]
        r.trace.append("physical")
        return True

    def provider(*a):
        assert r.guard and r.active
        return r.sdk

    monkeypatch.setattr(module, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(module, "native_storage_guard", guard)
    monkeypatch.setattr(module, "_locked", lambda *a: (run, attempt))
    monkeypatch.setattr(module, "_recover", recover)
    monkeypatch.setattr(module, "_provider_stopped", provider)
    monkeypatch.setattr(
        module,
        "reconcile_attempt_units",
        lambda *a: ContainmentSummary(len(r.rows), len(r.rows) if r.confirmed else 0),
    )
    monkeypatch.setattr(module, "append_event", lambda *a: r.events.append(a[3]))
    r.factory = Factory()
    r.settings = NS(storage_root="unused")
    return r


def call(r):
    return module.reconcile_structured_attempt(r.factory, r.attempt.id, r.settings)


def unit(profile=module.CONTROLLER):
    return NS(
        id=uuid4(), profile=profile, schema_version="mineagent.process-admission.v2"
    )


def test_prelaunch_cancel_commits_revocation_before_terminal(recovery):
    r = recovery
    old = r.attempt.lease_token
    assert call(r)
    assert r.attempt.lease_token != old
    assert r.run.status == "cancelled" and r.attempt.sandbox_stopped
    assert r.trace == ["begin", "commit", "begin", "commit"]
    assert r.events[0]["reservations_retained"] is True


def test_actual_units_outside_transactions_then_fresh_barriers(recovery):
    r = recovery
    r.rows = [unit(), unit(module.ENGINE)]
    assert call(r)
    assert r.trace == ["begin", "commit", "physical", "physical", "begin", "commit"]


def test_live_attempt_not_revoked(recovery):
    r = recovery
    r.run.status = "running"
    old = r.attempt.lease_token
    assert not call(r) and r.attempt.lease_token == old and not r.events


def test_expired_attempt_interrupted_not_succeeded(recovery):
    r = recovery
    r.run.status = "running"
    r.attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert call(r) and r.run.status == "interrupted" and r.run.generation == 1


@pytest.mark.parametrize("revision", ["026_rd_seeded_submissions", "028_future"])
def test_exact_revision_before_revocation(recovery, revision):
    r = recovery
    r.revision = revision
    old = r.attempt.lease_token
    assert not call(r) and r.attempt.lease_token == old


@pytest.mark.parametrize("sdk,confirmed", [(False, True), (True, False)])
def test_missing_positive_barrier_keeps_nonterminal(recovery, sdk, confirmed):
    r = recovery
    r.rows = [unit()]
    r.sdk = sdk
    r.confirmed = confirmed
    assert not call(r) and not r.events and not r.attempt.sandbox_stopped


def test_unknown_physical_identity_not_terminal(recovery, monkeypatch):
    r = recovery
    r.rows = [unit()]

    def unknown(*a):
        raise ProcessError("identity_unobserved")

    monkeypatch.setattr(module, "_recover", unknown)
    assert not call(r) and not r.events


@pytest.mark.parametrize("change", ["inventory", "token", "lease"])
def test_post_io_inventory_or_lease_change_rejects(recovery, monkeypatch, change):
    r = recovery
    r.rows = [unit()]

    def drift(*a):
        if change == "inventory":
            r.rows.append(unit(module.ENGINE))
        if change == "token":
            r.attempt.lease_token = uuid4()
        if change == "lease":
            r.attempt.lease_expires_at = utcnow() + timedelta(seconds=60)
        return True

    monkeypatch.setattr(module, "_recover", drift)
    assert not call(r) and not r.events


@pytest.mark.parametrize(
    "rows",
    [
        [unit("unknown")],
        [unit(module.ENGINE)],
        [unit(), unit()],
        [unit()] + [unit(module.ENGINE) for _ in range(4)],
    ],
)
def test_invalid_inventory_fails_before_revocation(recovery, rows):
    r = recovery
    r.rows = rows
    old = r.attempt.lease_token
    with pytest.raises(ProcessError, match="inventory"):
        call(r)
    assert r.attempt.lease_token == old and not r.events


@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "owner_id",
        "run_id",
        "request_sha256",
        "config_sha256",
        "request_canonical",
        "preflight_sha256",
        "idempotency_key",
    ],
)
def test_locked_reader_matches_original_submission(monkeypatch, mismatch):
    run = NS(
        id=uuid4(),
        owner_id=uuid4(),
        generation=1,
        status="running",
        idempotency_key="original",
    )
    attempt = NS(id=uuid4(), generation=1, finished_at=None)
    run.active_attempt_id = attempt.id
    spec = NS(
        submission_id=uuid4(),
        request_sha256="a" * 64,
        config_sha256="b" * 64,
        request_canonical='{"original":true}',
        preflight_sha256="c" * 64,
    )
    receipt = NS(
        owner_id=run.owner_id,
        run_id=run.id,
        idempotency_key=run.idempotency_key,
        request_sha256=spec.request_sha256,
        config_sha256=spec.config_sha256,
        request_canonical=spec.request_canonical,
        preflight_sha256=spec.preflight_sha256,
    )
    if mismatch:
        setattr(receipt, mismatch, "different")
    rows = iter([run, attempt])
    locks = []

    class Session:
        def execute(self, q):
            return NS(one_or_none=lambda: (run.owner_id, run.id))

        def scalar(self, q):
            assert q._for_update_arg.nowait
            return next(rows)

        def get(self, model, key):
            assert key == spec.submission_id
            return receipt

    monkeypatch.setattr(module, "decode_spec", lambda *a: (spec, None))
    monkeypatch.setattr(module, "_try_neutral_lock", lambda s, k: locks.append(k))
    if mismatch:
        with pytest.raises(ProcessError, match="submission"):
            module._locked(Session(), attempt.id)
    else:
        assert module._locked(Session(), attempt.id) == (run, attempt)
    assert locks == [f"submit:{run.owner_id}"]


def test_reconciler_uses_null_safe_exact_family_and_separate_barrier():
    import ast
    from pathlib import Path

    from sqlalchemy.dialects import postgresql

    from backend.app.models import ResearchRun

    tree = ast.parse(Path("workers/research/__main__.py").read_text())
    assignment = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "structured_family" for t in n.targets
        )
    )
    expression = eval(
        compile(ast.Expression(assignment.value), "family", "eval"),
        {"ResearchRun": ResearchRun},
    )
    sql = str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "mineagent.oe-structured-provider-run.v1" in sql and "IS true" in sql
    assert "strategy_search" in sql and "openevolve" in sql
    # Legacy selection must negate this null-safe expression; normal attempts
    # have their own post-transaction coordinator rather than legacy stopper.
    assert any(
        isinstance(n, ast.UnaryOp)
        and isinstance(n.op, ast.Invert)
        and isinstance(n.operand, ast.Name)
        and n.operand.id == "structured_family"
        for n in ast.walk(tree)
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "reconcile_structured_attempt"
        for n in ast.walk(tree)
    )
