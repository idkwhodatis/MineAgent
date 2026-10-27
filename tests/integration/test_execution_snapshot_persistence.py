"""Installed PostgreSQL constraints; collection is not executed acceptance.

Positive publication requires the actual complete RD/report runtime fixture.
These cases exercise shared arbitration and unbound metadata rejection directly.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_neutral_objects import object_setup, prepare  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_snapshot_cannot_steal_existing_object_reservation(object_setup, isolation):
    factory, fence, _ = object_setup
    _, reservation_id = prepare(object_setup)
    with (
        factory.kw["bind"]
        .connect()
        .execution_options(isolation_level=isolation) as connection
    ):
        with pytest.raises(DBAPIError):
            connection.execute(
                text("""INSERT INTO engine_object_reservation_consumers
                (reservation_id,owner_id,run_id,attempt_id,generation,consumer_kind,consumer_id)
                VALUES (:reservation,:owner,:run,:attempt,:generation,'execution_snapshot',:consumer)"""),
                {
                    "reservation": reservation_id,
                    "owner": fence.owner_id,
                    "run": fence.run_id,
                    "attempt": fence.attempt_id,
                    "generation": fence.generation,
                    "consumer": uuid4(),
                },
            )
        connection.rollback()
    with factory() as session:
        assert (
            session.scalar(
                text(
                    "SELECT consumer_kind FROM engine_object_reservation_consumers WHERE reservation_id=:id"
                ),
                {"id": reservation_id},
            )
            == "research_object"
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM factor_execution_snapshot_publications")
            )
            == 0
        )


def test_snapshot_installs_immediate_claim_and_deferred_full_chain(migrated_engine):
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT tgname,tgdeferrable,tginitdeferred
            FROM pg_trigger WHERE tgrelid='factor_execution_snapshot_publications'::regclass
            AND NOT tgisinternal""")
        ).all()
    triggers = {name: (deferred, initial) for name, deferred, initial in rows}
    assert triggers["claim_execution_snapshot_reservation"] == (False, False)
    assert any(deferred and initial for deferred, initial in triggers.values())


def test_unbound_execution_object_is_not_accepted(migrated_engine, owner):
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO factor_execution_snapshot_objects
            (id,owner_id,publication_id,object_kind,sha256,size_bytes,storage_key,created_at)
            VALUES (:id,:owner,:publication,'normalized_values',:sha,1,:key,now())"""),
            {
                "id": uuid4(),
                "owner": owner.user.id,
                "publication": uuid4(),
                "sha": "a" * 64,
                "key": f"factors/{owner.user.id}/objects/{'a' * 64}.parquet",
            },
        )
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM factor_execution_snapshot_objects")
            )
            == 0
        )
