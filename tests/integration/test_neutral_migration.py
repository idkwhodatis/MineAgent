"""Actual PostgreSQL regressions for trigger arguments and non-destructive DDL."""

from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.app.research_broker_services import initialize_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def downgrade(connection):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    config.attributes["connection"] = connection
    command.downgrade(config, "014_run_identity")


def test_neutral_zero_argument_immutability_trigger_rejects_changed_values(
    migrated_engine,
):
    with migrated_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "CREATE TEMP TABLE neutral_immutable_probe(id integer PRIMARY KEY, value jsonb NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER probe BEFORE UPDATE OR DELETE ON neutral_immutable_probe FOR EACH ROW EXECUTE FUNCTION mineagent_protect_neutral()"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO neutral_immutable_probe VALUES (1, '{\"record\": 1}')"
                )
            )
        for sql, parameters in (
            (
                "UPDATE neutral_immutable_probe SET value=CAST(:value AS jsonb)",
                {"value": '{"record": 2}'},
            ),
            ("DELETE FROM neutral_immutable_probe", {}),
        ):
            with pytest.raises(DBAPIError), connection.begin():
                connection.execute(text(sql), parameters)
        with connection.begin():
            assert connection.scalar(
                text("SELECT value FROM neutral_immutable_probe")
            ) == {"record": 1}
            connection.execute(text("DROP TABLE neutral_immutable_probe"))


@pytest.mark.parametrize(
    "parent",
    ["users", "research_runs", "execution_attempts", "research_budget_controls"],
)
def test_neutral_downgrade_prelocks_every_parent_without_wait(migrated_engine, parent):
    with migrated_engine.connect() as connection:
        entry_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    with migrated_engine.begin() as reader:
        reader.execute(text(f"LOCK TABLE {parent} IN ACCESS SHARE MODE"))
        with (
            pytest.raises(DBAPIError) as failure,
            migrated_engine.begin() as connection,
        ):
            downgrade(connection)
        assert failure.value.orig.sqlstate == "55P03"
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == entry_revision
        )


def test_neutral_downgrade_rejects_stale_snapshot_and_preserves_new_evidence(
    migrated_engine, owner, queued_run, claimed_attempt
):
    with migrated_engine.connect() as connection:
        entry_revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    fence = AttemptFence(
        owner.user.id,
        UUID(queued_run["id"]),
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )
    with migrated_engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as stale:
        assert stale.scalar(text("SELECT count(*) FROM research_budget_controls")) == 0
        with neutral_transaction(sessionmaker(migrated_engine), fence) as session:
            initialize_budget(session, fence, BudgetVector(calls=1))
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            downgrade(stale)
        stale.rollback()
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_budget_controls"))
            == 1
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == entry_revision
        )
    with (
        pytest.raises(DBAPIError, match="Neutral evidence prevents"),
        migrated_engine.begin() as connection,
    ):
        downgrade(connection)
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_budget_controls"))
            == 1
        )
