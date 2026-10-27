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
                    "SELECT mineagent_structured_raw_commitment(CAST(:raw AS text),CAST(:raw AS jsonb),CAST(:sha AS text),16777216)"
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
                    "SELECT mineagent_structured_raw_commitment(CAST(:raw AS text),CAST(:raw AS jsonb),CAST(:sha AS text),16777216)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT mineagent_analysis_raw_commitment(CAST(:raw AS text),CAST(:raw AS jsonb),CAST(:sha AS text),1048576)"
                ),
                {"raw": raw, "sha": hashlib.sha256(raw.encode()).hexdigest()},
            )
            is False
        )


def test_structured_version_immutable_and_not_legacy_strategy_actual_pg(
    migrated_engine,
):
    program = fixture()[0].program
    # Exercise the actual versioned interpreter, not just its rendered tokens.
    number_rule = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}
    cases = [
        ("0.5", number_rule, True),
        ("5e-324", number_rule, True),
        ("0.0", number_rule, False),
        ("-0.0", number_rule, False),
        ("1.0", number_rule, False),
        ("true", number_rule, False),
        ('"0.5"', number_rule, False),
        ("1e9999", {"type": "number"}, False),
        ("1e-9999", {"type": "number"}, False),
        ("true", {"type": "boolean"}, True),
        ("1", {"type": "boolean"}, False),
        ("1.0", {"type": "integer", "minimum": 0}, False),
        ('{"x":[0.5,true]}', {"type": "object", "additionalProperties": True}, True),
        ('{"x":1e9999}', {"type": "object", "additionalProperties": True}, False),
        ('{"x":1e-9999}', {"type": "object", "additionalProperties": True}, False),
    ]
    for rule in (
        {"type": "object"},
        {"type": "object", "additionalProperties": False},
        {"type": "object", "additionalProperties": True, "properties": {}},
    ):
        cases.append(('{"x":0.5}', rule, False))
    deep = {}
    for _ in range(26):
        deep = {"x": deep}
    cases.append(
        (json.dumps(deep), {"type": "object", "additionalProperties": True}, False)
    )
    with migrated_engine.begin() as connection:
        for raw, rule, expected in cases:
            actual = connection.scalar(
                text(
                    "SELECT mineagent_rd_schema(CAST(:raw AS jsonb),CAST(:rule AS jsonb),'{}'::jsonb,0)"
                ),
                {"raw": raw, "rule": json.dumps(rule)},
            )
            assert actual is expected, (raw, rule)
        for weight in (0.0, -0.0, -0.5, 1.1, True, "0.5"):
            invalid = {**program.model_dump(mode="json"), "maximum_weight": weight}
            assert (
                connection.scalar(
                    text(
                        "SELECT mineagent_structured_contract('program',CAST(:value AS jsonb))"
                    ),
                    {"value": json.dumps(invalid)},
                )
                is False
            )
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
