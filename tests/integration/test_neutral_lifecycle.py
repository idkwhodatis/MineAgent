"""Actual PostgreSQL tests for the neutral short-transaction authority boundary."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence

pytestmark = pytest.mark.postgres


@pytest.fixture
def neutral_fence(owner, queued_run, claimed_attempt):
    return AttemptFence(
        owner.user.id,
        UUID(queued_run["id"]),
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )


def test_neutral_nested_transaction_never_erases_outer_work(
    migrated_engine, neutral_fence
):
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    with neutral_transaction(factory, neutral_fence) as session:
        tx = session.get_transaction()
        session.execute(
            text("CREATE TEMP TABLE neutral_pending(value integer) ON COMMIT DROP")
        )
        session.execute(text("INSERT INTO neutral_pending VALUES (17)"))
        with (
            pytest.raises(DomainError, match="Neutral execution"),
            neutral_transaction(factory, neutral_fence),
        ):
            pass
        assert session.get_transaction() is tx
        assert session.scalar(text("SELECT value FROM neutral_pending")) == 17
        require_neutral_transaction(session, neutral_fence)


def test_neutral_helper_cannot_commit_or_reuse_released_authority(
    migrated_engine, neutral_fence
):
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    with neutral_transaction(factory, neutral_fence) as session:
        with pytest.raises(DomainError):
            session.commit()
        require_neutral_transaction(session, neutral_fence)
    with pytest.raises(DomainError):
        require_neutral_transaction(session, neutral_fence)


@pytest.mark.parametrize(
    "field", ["owner_id", "run_id", "attempt_id", "lease_token", "generation"]
)
def test_neutral_real_scope_substitution_is_rejected(
    migrated_engine, neutral_fence, field
):
    bad = replace(neutral_fence, **{field: 2 if field == "generation" else uuid4()})
    with (
        pytest.raises(DomainError),
        neutral_transaction(sessionmaker(migrated_engine), bad),
    ):
        pytest.fail("substituted scope admitted")


def test_neutral_resource_scope_is_not_implicitly_promoted(
    migrated_engine, neutral_fence
):
    with neutral_transaction(sessionmaker(migrated_engine), neutral_fence) as session:
        with pytest.raises(DomainError):
            require_neutral_transaction(
                session, neutral_fence, resource_scope="objects"
            )
        require_neutral_transaction(session, neutral_fence)


def test_neutral_database_loss_does_not_reconnect(migrated_engine, neutral_fence):
    with (
        pytest.raises(DomainError),
        neutral_transaction(sessionmaker(migrated_engine), neutral_fence) as session,
    ):
        session.connection().invalidate()
        require_neutral_transaction(session, neutral_fence)
    # A new unrelated session still functions; lost authority was not recovered.
    with Session(migrated_engine) as session:
        assert session.scalar(text("SELECT 1")) == 1
