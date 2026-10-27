"""Recovery transaction/order doubles; not actual Docker/PG acceptance."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from packages.research_runtime.process_contracts import ContainmentSummary
from workers.research import rdagent_recovery as module


@pytest.fixture(
    params=[
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
    ]
)
def recovery(request, monkeypatch):
    run = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), status="cancel_requested", generation=1
    )
    attempt = SimpleNamespace(
        id=uuid4(),
        lease_token=uuid4(),
        lease_expires_at=utcnow() + timedelta(seconds=100),
        generation=1,
        sandbox_stopped=False,
        finished_at=None,
    )
    state = SimpleNamespace(
        revision=request.param,
        run=run,
        attempt=attempt,
        admissions=[],
        events=[],
        transactions=[],
        contained=[],
    )

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        @contextmanager
        def begin(self):
            state.transactions.append("begin")
            yield SimpleNamespace(
                scalars=lambda q: SimpleNamespace(
                    all=lambda: (
                        [state.revision]
                        if "alembic_version" in str(q)
                        else state.admissions
                    )
                )
            )
            state.transactions.append("commit")

    state.factory = Factory()

    @contextmanager
    def lifecycle(*a):
        yield

    monkeypatch.setattr(module, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(module, "_locked", lambda *a, **kw: (run, attempt))
    from backend.app import rdagent_provider_containment

    monkeypatch.setattr(
        rdagent_provider_containment,
        "require_source_identity",
        lambda _: SimpleNamespace(model_profile="fixture.rdagent.v1"),
    )
    monkeypatch.setattr(module, "append_event", lambda *a: state.events.append(a[3]))
    monkeypatch.setattr(
        module,
        "reconcile_attempt_units",
        lambda *a: ContainmentSummary(len(state.admissions), len(state.admissions)),
    )
    state.settings = SimpleNamespace(storage_root="unused")
    return state


def test_cancel_before_any_admission_revokes_then_finishes(recovery):
    r = recovery
    old = r.attempt.lease_token
    assert module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.run.status == r.attempt.status == "cancelled" and r.attempt.sandbox_stopped
    assert r.attempt.lease_token != old and r.events[0]["reservations_retained"] is True
    assert r.transactions == ["begin", "commit", "begin", "commit"]


def test_live_uncancelled_worker_not_revoked(recovery):
    r = recovery
    r.run.status = "running"
    old = r.attempt.lease_token
    assert not module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.attempt.lease_token == old and not r.events


def test_expired_worker_interrupted_not_retried(recovery):
    r = recovery
    r.run.status = "running"
    r.attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.run.status == "interrupted" and r.run.generation == 1


def test_unknown_unit_stays_nonterminal_and_held(recovery, monkeypatch):
    from workers.research import numeric_process

    r = recovery
    r.admissions.append(SimpleNamespace(id=uuid4(), profile="unknown"))

    def fail(*a):
        raise ValueError("no exact profile")

    monkeypatch.setattr(numeric_process, "recover_numeric_unit", fail)
    assert not module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert (
        r.run.status == "cancel_requested"
        and not r.attempt.sandbox_stopped
        and not r.events
    )


def test_recovery_receipt_is_not_itself_terminal_authority(recovery, monkeypatch):
    from workers.research import numeric_process

    r = recovery
    r.admissions.append(SimpleNamespace(id=uuid4(), profile="numeric"))

    def contain(*a):
        assert r.transactions == ["begin", "commit"]
        r.contained.append(a[-1])

    monkeypatch.setattr(numeric_process, "recover_numeric_unit", contain)
    monkeypatch.setattr(
        module, "reconcile_attempt_units", lambda *a: ContainmentSummary(1, 0)
    )
    assert not module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.contained and not r.events and not r.attempt.sandbox_stopped


def test_unknown_successor_never_revokes_or_records_containment(recovery):
    r = recovery
    r.revision = "026_future"
    token = r.attempt.lease_token
    assert not module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.attempt.lease_token == token
    assert not r.events and not r.contained


def test_docker_confirmed_without_sdk_proof_is_not_terminal(recovery, monkeypatch):
    from backend.app import rdagent_provider_containment

    r = recovery
    monkeypatch.setattr(
        rdagent_provider_containment, "provider_attempt_stopped", lambda *a: False
    )
    assert not module.reconcile_rd_attempt(r.factory, r.attempt.id, r.settings)
    assert r.run.status == "cancel_requested"
    assert not r.attempt.sandbox_stopped and r.attempt.finished_at is None
    assert not r.events
