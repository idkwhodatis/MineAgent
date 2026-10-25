from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from backend.app.research_lifecycle import _check_neutral_fence, neutral_transaction
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence


def fixture():
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    run = SimpleNamespace(
        id=fence.run_id,
        owner_id=fence.owner_id,
        active_attempt_id=fence.attempt_id,
        generation=1,
        status="running",
        purpose="legacy_strategy",
        engine="legacy",
        config={"schema_version": "tradegrad.run.v1"},
    )
    attempt = SimpleNamespace(
        id=fence.attempt_id,
        run_id=fence.run_id,
        generation=1,
        lease_token=fence.lease_token,
        lease_expires_at=utcnow() + timedelta(minutes=1),
        status="running",
        finished_at=None,
    )
    return fence, run, attempt


@pytest.mark.parametrize("generation", [True, False, 0, -1, 1.0, "1"])
def test_attempt_fence_requires_exact_positive_integer(generation):
    with pytest.raises(ValueError):
        AttemptFence(uuid4(), uuid4(), uuid4(), generation, uuid4())


def test_attempt_fence_rejects_unparsed_authority():
    with pytest.raises(ValueError):
        AttemptFence(str(uuid4()), uuid4(), uuid4(), 1, uuid4())


@pytest.mark.parametrize(
    "field", ["owner_id", "run_id", "attempt_id", "lease_token", "generation"]
)
def test_fence_rejects_each_scope_substitution(field):
    fence, run, attempt = fixture()
    with pytest.raises(DomainError):
        _check_neutral_fence(
            run,
            attempt,
            replace(fence, **{field: 2 if field == "generation" else uuid4()}),
        )


@pytest.mark.parametrize(
    "status", ["cancel_requested", "pause_requested", "succeeded", "failed", "queued"]
)
def test_fence_cannot_authorize_work_after_control_stop(status):
    fence, run, attempt = fixture()
    run.status = status
    with pytest.raises(DomainError):
        _check_neutral_fence(run, attempt, fence)


def test_fence_rejects_expired_or_finished_attempt():
    fence, run, attempt = fixture()
    _check_neutral_fence(run, attempt, fence)
    attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(DomainError):
        _check_neutral_fence(run, attempt, fence)


def test_bad_scope_fails_before_factory_or_database():
    fence, _, _ = fixture()

    def forbidden():
        raise AssertionError("must not create session")

    with (
        pytest.raises(DomainError),
        neutral_transaction(forbidden, fence, resource_scope="arbitrary"),
    ):
        pass


def test_factory_cannot_disguise_and_roll_back_a_caller_owned_session():
    from sqlalchemy.orm import Session

    fence, _, _ = fixture()
    with Session() as session, session.begin() as pending:
        with pytest.raises(DomainError), neutral_transaction(lambda: session, fence):
            pytest.fail("Caller-owned session admitted")
        assert session.get_transaction() is pending
        assert pending.is_active
