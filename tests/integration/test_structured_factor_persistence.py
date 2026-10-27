"""Actual PostgreSQL025 codec/table tests; no engine execution is inferred."""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.models import User
from backend.app.structured_factor_models import StructuredStrategyVersion
from packages.factors.contracts import canonical_bytes, digest
from tests.structured.test_operator import fixture

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, -1.0, 1e-7, 1e20, 5e-324, -5e-324])
def test_structured_raw_float_commitment_actual_pg(migrated_engine, value):
    raw = canonical_bytes({"value": value}).decode("ascii")
    with migrated_engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_structured_raw_commitment(:raw,CAST(:raw AS jsonb),:sha,16777216)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is True
        )
        assert (
            connection.scalar(
                text("SELECT mineagent_structured_raw_json(CAST(:raw AS json))"),
                {"raw": raw},
            )
            == raw
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e9999}',
        '{"value":1e-9999}',
        '{"value":0.0,"value":1.0}',
        '{ "value":0.0}',
    ],
)
def test_structured_raw_unsafe_actual_pg(migrated_engine, raw):
    # Pass a benign parsed payload; malformed/nonfinite raw must never be accepted.
    with migrated_engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_structured_raw_commitment(:raw,'{}'::jsonb,:sha,16777216)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is False
        )


def test_structured_raw_large_original_bytes_not_old_one_mib_codec(migrated_engine):
    raw = json.dumps(["x" * 40] * 30000, separators=(",", ":"))
    assert 1048576 < len(raw) < 16777216
    with migrated_engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_structured_raw_commitment(:raw,CAST(:raw AS jsonb),:sha,16777216)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(:raw,CAST(:raw AS jsonb),:sha,1048576)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is False
        )


def test_structured_version_immutable_and_not_legacy_strategy_actual_pg(
    migrated_engine,
):
    program = fixture()[0].program
    owner_id, version_id = uuid4(), uuid4()
    with Session(migrated_engine) as session:
        session.add(
            User(
                id=owner_id,
                email=uuid4().hex + "@example.test",
                hashed_password="no-login",
                full_name="Structured version test",
            )
        )
        session.flush()
        session.add(
            StructuredStrategyVersion(
                id=version_id,
                owner_id=owner_id,
                parent_version_id=None,
                program=program.model_dump(mode="json"),
                program_canonical=canonical_bytes(program).decode("ascii"),
                program_sha256=digest(program),
            )
        )
        session.commit()
    with pytest.raises(DBAPIError), migrated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE structured_strategy_versions SET program_sha256=:sha WHERE id=:id"
            ),
            {"sha": "f" * 64, "id": version_id},
        )
    with migrated_engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM strategy_versions WHERE id=:id"),
                {"id": version_id},
            )
            == 0
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM research_process_admissions"))
            == 0
        )
