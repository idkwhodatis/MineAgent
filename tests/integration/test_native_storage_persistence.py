"""Genuine PostgreSQL checks; collect-only is not execution/acceptance.

A complete native publication fixture is provided only by the real RD/numeric
integration. These tests already exercise installed constraints and immediate
cross-family arbitration without inventing source/stop evidence.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_neutral_objects import object_setup, prepare  # noqa: F401

# Imported pytest fixture is intentionally injected by name.
# ruff: noqa: F811

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_native_claim_cannot_steal_committed_research_object_reservation(
    object_setup, isolation
):
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
              VALUES (:reservation,:owner,:run,:attempt,:generation,'native_hdf',:consumer)"""),
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
            session.scalar(text("SELECT count(*) FROM factor_native_publications")) == 0
        )


def test_native_object_without_exact_publication_outcome_is_rejected(
    migrated_engine, owner
):
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO factor_native_objects
              (id,owner_id,object_kind,sha256,size_bytes,storage_key,media_type,created_at)
              VALUES (:id,:owner,'native_hdf',:sha,1,:key,'application/x-hdf5',now())"""),
            {
                "id": uuid4(),
                "owner": owner.user.id,
                "sha": "a" * 64,
                "key": f"factors/{owner.user.id}/objects/{'a' * 64}.h5",
            },
        )
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM factor_native_objects")) == 0
        )


def test_native_schema_uses_immediate_claim_and_deferred_closure(migrated_engine):
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT tgname,tgdeferrable,tginitdeferred
            FROM pg_trigger WHERE tgrelid='factor_native_publications'::regclass
            AND NOT tgisinternal""")
        ).all()
    values = {name: (deferred, initial) for name, deferred, initial in rows}
    assert values["claim_native_reservation"] == (False, False)
    assert values["check_native_consumer"] == (True, True)
    assert values["check_factor_native_publications_native"] == (True, True)


def test_native_outcome_retains_full_charge_trigger_on_reservation(migrated_engine):
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text("""SELECT count(*) FROM pg_trigger
            WHERE tgname='check_research_budget_reservations_native'
            AND tgrelid='research_budget_reservations'::regclass
            AND tgdeferrable AND tginitdeferred""")
            )
            == 1
        )


@pytest.mark.parametrize(
    "value",
    [
        {"输出": "价格😀", "ascii": 'quote" slash\\ newline\n tab\t DEL\x7f'},
        {"size": 16777216, "negative": -1000, "list": [True, None, "𐐷", "é"]},
    ],
)
def test_native_sql_commitment_matches_bounded_unicode_integer_python_codec(
    migrated_engine, value
):
    import json

    from packages.factors.contracts import canonical_bytes

    with migrated_engine.connect() as connection:
        observed = connection.scalar(
            text("SELECT mineagent_native_metadata(CAST(:value AS jsonb))"),
            {"value": json.dumps(value)},
        )
    assert observed == canonical_bytes(value).decode("ascii")


def test_native_sql_commitment_refuses_float_codec_ambiguity(migrated_engine):
    with migrated_engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("SELECT mineagent_native_metadata(CAST(:value AS jsonb))"),
            {"value": '{"x":1.25}'},
        )
