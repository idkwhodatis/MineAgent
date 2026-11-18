"""Offline scheduling/source evidence, not PostgreSQL execution evidence."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes.rd_admission_expiry_observation import (
    EXPIRY_QUERY,
    CommittedExpiryObserver,
)
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected


def setup_observer():
    state = {"duration": 10, "sql": [], "closed": 0, "error": None}
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class FakeSession(Session):
        def connection(self, *, execution_options):
            state["options"] = execution_options
            return self

        def execute(self, statement, params=None):
            state["sql"].append((str(statement), params))
            if state["error"] is not None:
                raise state["error"]
            return self

        def one_or_none(self):
            if state.get("missing"):
                return None
            return now + timedelta(seconds=state["duration"]), now

        def close(self):
            state["closed"] += 1
            super().close()

    factory = sessionmaker(class_=FakeSession)
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    return CommittedExpiryObserver(factory, fence), factory, fence, state


def test_single_read_only_observation_and_query_latency_charged():
    observer, factory, fence, state = setup_observer()
    ticks = iter([100.0, 103.0])
    ticket = observer.observe(factory, fence, clock=lambda: next(ticks))
    assert ticket.cutoff == 110
    assert ticket.remaining(factory, fence, clock=lambda: 104) == 6
    assert len(state["sql"]) == 1 and state["closed"] == 1
    assert state["options"] == {
        "isolation_level": "READ COMMITTED",
        "postgresql_readonly": True,
    }
    assert state["sql"][0][1]["lease_token"] == fence.lease_token


def test_new_ticket_may_observe_renewal_old_ticket_never_refreshes():
    observer, factory, fence, state = setup_observer()
    old = observer.observe(factory, fence, clock=lambda: 100)
    state["duration"] = 120
    fresh = observer.observe(factory, fence, clock=lambda: 105)
    assert old.cutoff == 110 and fresh.cutoff == 225
    with pytest.raises(AdmissionRejected, match="expired"):
        old.remaining(factory, fence, clock=lambda: 111)
    with pytest.raises(FrozenInstanceError):
        old.cutoff = fresh.cutoff
    assert len(state["sql"]) == 2


def test_exact_binding_before_sql_and_ticket_use():
    observer, factory, fence, state = setup_observer()
    for other_factory, other_fence in (
        (sessionmaker(), fence),
        (factory, replace(fence, lease_token=UUID(int=9))),
    ):
        with pytest.raises(AdmissionRejected, match="binding_mismatch"):
            observer.observe(other_factory, other_fence)
    assert state["sql"] == []
    ticket = observer.observe(factory, fence, clock=lambda: 1)
    with pytest.raises(AdmissionRejected, match="binding_mismatch"):
        ticket.remaining(factory, replace(fence, generation=2))


@pytest.mark.parametrize("mode", ["missing", "expired", "latency", "sql", "clock"])
def test_invalid_observations_fail_closed_sanitized(mode):
    observer, factory, fence, state = setup_observer()
    ticks = [100, 100]
    if mode == "missing":
        state["missing"] = True
    elif mode == "expired":
        state["duration"] = -1
    elif mode == "latency":
        ticks[1] = 111
    elif mode == "sql":
        state["error"] = RuntimeError("private database params secret-token")
    else:
        ticks[0] = float("nan")
    clock = iter(ticks)
    with pytest.raises(AdmissionRejected) as caught:
        observer.observe(factory, fence, clock=lambda: next(clock))
    assert str(caught.value) == "observation_rejected"
    assert caught.value.__suppress_context__


def test_cancellation_identity_preserved_and_session_closed():
    observer, factory, fence, state = setup_observer()
    failure = KeyboardInterrupt()
    state["error"] = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        observer.observe(factory, fence, clock=lambda: 1)
    assert caught.value is failure and state["closed"] == 1


def test_source_has_full_fence_predicates_no_lock_or_runtime_activation():
    for clause in (
        "r.owner_id=:owner_id",
        "r.active_attempt_id=:attempt_id",
        "r.generation=:generation",
        "a.generation=:generation",
        "a.lease_token=:lease_token",
        "a.finished_at IS NULL",
        "clock_timestamp()",
    ):
        assert clause in EXPIRY_QUERY
    for forbidden in ("FOR UPDATE", "FOR SHARE", "pg_advisory", "UPDATE ", "INSERT "):
        assert forbidden not in EXPIRY_QUERY
    root = Path(__file__).resolve().parents[2]
    for directory in ("backend", "workers", "runtimes"):
        assert not any(
            "rd_admission_expiry_observation" in p.read_text()
            for p in (root / directory).rglob("*.py")
        )
