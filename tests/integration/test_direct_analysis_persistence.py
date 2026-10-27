"""Genuine PostgreSQL plan/trigger tests; no process or Qlib execution claim.

The pinned digests here are declaration-only test data. Production registration
is still closed and these tests never call a launcher or fabricate a result.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.models import User

pytestmark = pytest.mark.postgres


@pytest.fixture
def declared_plan(migrated_engine, settings, monkeypatch):
    from rd_configured_controller_fixture import prepare_configured_generation

    for role in ("CANDIDATE", "NORMALIZER", "ORCHESTRATOR", "ANALYSIS"):
        monkeypatch.setenv("MINEAGENT_" + role + "_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("MINEAGENT_QLIB_INSTALLED_IMAGE", "sha256:" + "b" * 64)
    with Session(migrated_engine, expire_on_commit=False) as session:
        user = User(
            id=uuid4(),
            email=uuid4().hex + "@example.test",
            hashed_password="test-no-login",
            full_name="Plan constraint test",
        )
        session.add(user)
        session.commit()
    factory, fence = prepare_configured_generation(migrated_engine, settings, user.id)
    return factory, fence, settings


def test_declared_plan_precedes_generation_and_has_no_fabricated_result(declared_plan):
    factory, fence, _ = declared_plan
    with factory() as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM factor_analysis_plans WHERE run_id=:id"),
                {"id": fence.run_id},
            )
            == 1
        )
        for table in (
            "factor_generation_starts",
            "factor_direct_analysis_intents",
            "factor_direct_analysis_outcomes",
            "research_process_admissions",
        ):
            assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize(
    "column,value",
    [
        ("objective_sha256", "c" * 64),
        ("qlib_installed_parent_image_id", "sha256:" + "c" * 64),
        ("source_policy_sha256", "c" * 64),
    ],
)
def test_declared_plan_is_immutable_in_actual_pg(declared_plan, column, value):
    factory, fence, _ = declared_plan
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(f"UPDATE factor_analysis_plans SET {column}=:value WHERE run_id=:id"),
            {"id": fence.run_id, "value": value},
        )


def test_downgrade_rejects_even_preexecution_analysis_plan(declared_plan):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    factory, _, _ = declared_plan
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/alembic"))
    with pytest.raises(DBAPIError), factory.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "021_execution_snapshots")
    with factory() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "022_factor_analysis"
        )


def test_022_installs_frozen_semantic_and_immutable_triggers(migrated_engine):
    with migrated_engine.connect() as connection:
        names = set(
            connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            ).scalars()
        )
        assert {
            "analysis_plan_before_insert",
            "analysis_generation_order",
            "analysis_plan_consistent",
            "direct_analysis_consistent",
            "configured_input_consistent",
            "rd_analysis_generation_closed",
        } <= names
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conname='ck_process_v2_unregistered' AND pg_get_constraintdef(oid) LIKE '%mineagent.qlib-direct-analysis.v1%'"
                )
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, -1.0, 1e-7, 1e-20, 5e-324, -5e-324])
def test_actual_pg_lossless_float_commitments(migrated_engine, value):
    import hashlib
    import json

    from packages.factors.contracts import canonical_bytes

    raw = canonical_bytes({"value": value}).decode("ascii")
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": json.dumps({"value": value}),
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": '{"value":0.5}',
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is False
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e999}',
        '{"value":1e-999}',
        '{"value":0.0,"value":1.0}',
        '{ "value":0.0}',
    ],
)
def test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical(migrated_engine, raw):
    import hashlib

    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:payload AS jsonb),:sha,1048576)"
                ),
                {
                    "raw": raw,
                    "payload": "{}" if "NaN" in raw or "Infinity" in raw else raw,
                    "sha": hashlib.sha256(raw.encode()).hexdigest(),
                },
            )
            is False
        )
