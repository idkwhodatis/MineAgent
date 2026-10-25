"""Real PostgreSQL classification, immutable identity and historical upgrade."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = (
    "tradegrad.run.v1",
    "tradegrad.portable.run.v1",
    "tradegrad.final-evaluation.v1",
    "tradegrad.portable.final-evaluation.v1",
    "tradegrad.independent-evaluation.v1",
    "tradegrad.portable.independent-evaluation.v1",
    "tradegrad.comparison.v1",
)


def migrate(connection, revision, *, downgrade=False):
    cfg = Config(str(ROOT / "backend/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    cfg.attributes["connection"] = connection
    (command.downgrade if downgrade else command.upgrade)(cfg, revision)


def seed_config(owner):
    return {
        "task": "cn_time",
        "dataset_version_id": str(owner.dataset.id),
        "strategy_version_id": str(owner.strategy.id),
        "start_date": "2024-01-01",
        "end_date": "2024-04-01",
        "profile": "corrected_explicit_config",
        "market": "CN",
        "dataset_sha256": owner.dataset.content_sha256,
        "strategy_sha256": owner.strategy.content_sha256,
        "execution_mode": "seed_evaluation",
        "is_synthetic": True,
    }


def insert_historical(connection, owner, config):
    run_id = uuid4()
    connection.execute(
        text("""INSERT INTO research_runs
        (id,owner_id,idempotency_key,request_sha256,config,dataset_version_id,
         strategy_version_id,status,generation,next_event_seq,created_at,updated_at)
        VALUES (:id,:owner,:key,:hash,CAST(:config AS jsonb),:dataset,:strategy,
                'succeeded',1,0,now(),now())"""),
        {
            "id": run_id,
            "owner": owner.user.id,
            "key": uuid4().hex,
            "hash": "a" * 64,
            "config": json.dumps(config),
            "dataset": owner.dataset.id,
            "strategy": owner.strategy.id,
        },
    )
    return run_id


def read_identity(connection, run_id):
    return connection.execute(
        text("""SELECT purpose,engine,config::text,
      request_sha256,dataset_version_id,strategy_version_id
      FROM research_runs WHERE id=:id"""),
        {"id": run_id},
    ).one()


def test_real_legacy_run_identity_upgrade_preserves_bytes(migrated_engine, owner):
    with migrated_engine.begin() as connection:
        migrate(connection, "013_factor_import", downgrade=True)
        assert "purpose" not in {
            c["name"] for c in inspect(connection).get_columns("research_runs")
        }
        run_id = insert_historical(connection, owner, seed_config(owner))
        before = connection.execute(
            text("""SELECT config::text,request_sha256,
          dataset_version_id,strategy_version_id FROM research_runs WHERE id=:id"""),
            {"id": run_id},
        ).one()
        binding_trigger = connection.scalar(
            text("SELECT pg_get_functiondef('protect_m5_run_binding()'::regprocedure)")
        )
        migrate(connection, "014_run_identity")
        after = read_identity(connection, run_id)
        assert after[:2] == ("legacy_strategy", "legacy")
        assert after[2:] == tuple(before)
        assert (
            connection.scalar(
                text(
                    "SELECT pg_get_functiondef('protect_m5_run_binding()'::regprocedure)"
                )
            )
            == binding_trigger
        )
        for schema in SCHEMAS:
            # Routing recognition only; family-specific validity stays with the
            # existing validators and real legacy fixture regression suites.
            assert (
                connection.scalar(
                    text("SELECT mineagent_legacy_run_config(CAST(:value AS jsonb))"),
                    {"value": json.dumps({"schema_version": schema})},
                )
                is True
            )
        migrate(connection, "013_factor_import", downgrade=True)
        migrate(connection, "014_run_identity")
        assert read_identity(connection, run_id) == after


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"schema_version": None},
        {"schema_version": 1},
        {"schema_version": "mineagent.factor-research.v1"},
        {"schema_version": "tradegrad.run.v2"},
        [],
        None,
    ],
)
def test_real_identity_upgrade_rejects_unknown_history(migrated_engine, owner, bad):
    with migrated_engine.begin() as connection:
        migrate(connection, "013_factor_import", downgrade=True)
        run_id = insert_historical(connection, owner, bad)
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        migrate(connection, "014_run_identity")
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "013_factor_import"
        )
        assert "purpose" not in {
            c["name"] for c in inspect(connection).get_columns("research_runs")
        }
        assert (
            connection.scalar(
                text("SELECT config FROM research_runs WHERE id=:id"), {"id": run_id}
            )
            == bad
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", None),
        ("engine", None),
        ("purpose", "factor_research"),
        ("engine", "rdagent"),
        ("purpose", "unknown"),
        ("engine", "unknown"),
        ("strategy_version_id", None),
        ("request_sha256", "b" * 64),
    ],
)
def test_real_run_identity_and_old_inputs_are_immutable(
    migrated_engine, owner, field, value
):
    assert field in {"purpose", "engine", "strategy_version_id", "request_sha256"}
    with migrated_engine.begin() as connection:
        run_id = insert_historical(connection, owner, seed_config(owner))
        before = read_identity(connection, run_id)
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE research_runs SET {field}=:value WHERE id=:id"),
            {"value": value, "id": run_id},
        )
    with migrated_engine.connect() as connection:
        assert read_identity(connection, run_id) == before


@pytest.mark.parametrize(
    "change",
    [
        {"profile": "unknown"},
        {"execution_mode": "factor_research"},
        {"is_synthetic": "true"},
        {"market": "US"},
        {"unexpected": 1},
        {"dataset_sha256": "bad"},
        {"start_date": "2024-02-30"},
        {"schema_version": None},
        {"strategy_version_id": "not-a-uuid"},
        {"end_date": "2023-01-01"},
    ],
)
def test_real_seed_identity_rejects_malformed_records(migrated_engine, owner, change):
    config = seed_config(owner) | change
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        insert_historical(connection, owner, config)


@pytest.mark.parametrize(
    "purpose,engine",
    [
        (None, "legacy"),
        ("legacy_strategy", None),
        ("unknown", "legacy"),
        ("legacy_strategy", "unknown"),
        ("factor_research", "rdagent"),
    ],
)
def test_real_run_identity_insert_rejects_unregistered_pairs(
    migrated_engine, owner, purpose, engine
):
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO research_runs
          (id,owner_id,idempotency_key,request_sha256,config,dataset_version_id,
           strategy_version_id,status,generation,next_event_seq,created_at,updated_at,
           purpose,engine)
          VALUES (:id,:owner,:key,:hash,CAST(:config AS jsonb),:dataset,:strategy,
                  'queued',1,0,now(),now(),:purpose,:engine)"""),
            {
                "id": uuid4(),
                "owner": owner.user.id,
                "key": uuid4().hex,
                "hash": "a" * 64,
                "config": json.dumps(seed_config(owner)),
                "dataset": owner.dataset.id,
                "strategy": owner.strategy.id,
                "purpose": purpose,
                "engine": engine,
            },
        )
