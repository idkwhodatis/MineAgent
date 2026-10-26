"""Real PostgreSQL lock exclusion for the combined factor/research boundary."""

import hashlib
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.resource_scopes import advisory_lock_plan

pytestmark = pytest.mark.postgres


def _try(connection, name):
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    return connection.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
    )


@pytest.fixture
def combined_fence(owner, queued_run, claimed_attempt):
    return AttemptFence(
        owner.user.id,
        UUID(queued_run["id"]),
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )


@pytest.mark.parametrize("busy_index", range(5))
def test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks(
    migrated_engine, combined_fence, busy_index
):
    plan = advisory_lock_plan(combined_fence.owner_id, "factor_objects")
    with migrated_engine.begin() as blocker:
        assert _try(blocker, plan[busy_index]) is True
        with (
            pytest.raises(DomainError),
            neutral_transaction(
                sessionmaker(migrated_engine),
                combined_fence,
                resource_scope="factor_objects",
            ),
        ):
            pytest.fail("Busy combined publication was admitted")
        # Failed admission released every acquired prefix lock; no helper retry
        # is allowed to retain partial quota authority across transactions.
        with migrated_engine.begin() as observer:
            for index, name in enumerate(plan):
                assert _try(observer, name) is (index != busy_index)


def test_combined_transaction_holds_all_locks_and_only_object_capability(
    migrated_engine, combined_fence
):
    plan = advisory_lock_plan(combined_fence.owner_id, "factor_objects")
    with neutral_transaction(
        sessionmaker(migrated_engine),
        combined_fence,
        resource_scope="factor_objects",
    ) as session:
        require_neutral_transaction(session, combined_fence, resource_scope="objects")
        require_neutral_transaction(
            session, combined_fence, resource_scope="factor_objects"
        )
        with pytest.raises(DomainError):
            require_neutral_transaction(
                session, combined_fence, resource_scope="processes"
            )
        with migrated_engine.begin() as observer:
            assert all(_try(observer, name) is False for name in plan)
    with migrated_engine.begin() as observer:
        assert all(_try(observer, name) is True for name in plan)


def test_plain_objects_cannot_gain_factor_publication_authority(
    migrated_engine, combined_fence
):
    with (
        neutral_transaction(
            sessionmaker(migrated_engine), combined_fence, resource_scope="objects"
        ) as session,
        pytest.raises(DomainError),
    ):
        require_neutral_transaction(
            session, combined_fence, resource_scope="factor_objects"
        )


def test_factor_numeric_explicitly_supplies_both_without_widening_old_scope(
    migrated_engine, combined_fence
):
    with neutral_transaction(
        sessionmaker(migrated_engine), combined_fence, resource_scope="factor_numeric"
    ) as session:
        for required in ("factor_numeric", "factor_objects", "objects", "processes"):
            require_neutral_transaction(
                session, combined_fence, resource_scope=required
            )
    for old in ("factor_objects", "objects", "processes"):
        with (
            neutral_transaction(
                sessionmaker(migrated_engine), combined_fence, resource_scope=old
            ) as session,
            pytest.raises(DomainError),
        ):
            require_neutral_transaction(
                session, combined_fence, resource_scope="factor_numeric"
            )
