"""Actual migration/backfill and direct-SQL gates; no container execution claims."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_research_process_recovery import (
    _admit_fixture_process,
)
from test_research_process_recovery import (
    process_fence_fixture as shared_process_fence_fixture,
)

from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import as_admission
from packages.research_runtime.process_contracts import canonical

process_fence_fixture = shared_process_fence_fixture
pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
V1 = "mineagent.process-admission.v1"
V2 = "mineagent.process-admission.v2"


def migrate(engine, revision, *, downgrade=False):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture
def admission(migrated_engine, process_fence_fixture, monkeypatch, settings):
    # Synthetic configured identity only: admission has NOT been physically launched.
    image = "sha256:" + "1" * 64
    monkeypatch.setenv("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE", image)
    return _admit_fixture_process(*process_fence_fixture, image, settings=settings)[0]


def test_real_versioned_upgrade_backfills_old_admission_without_commitment_drift(
    migrated_engine, admission
):
    expected = canonical(admission.public())
    migrate(migrated_engine, "015_neutral_primitives", downgrade=True)
    with migrated_engine.connect() as conn:
        before = conn.scalar(
            text("SELECT to_jsonb(t) FROM research_process_admissions t")
        )
        assert "schema_version" not in before and "ipc_policy" not in before
    migrate(migrated_engine, "016_process_versioning")
    with Session(migrated_engine) as session:
        row = session.scalar(select(ResearchProcessAdmission))
        assert row.schema_version == V1 and row.ipc_policy == "host_ticket"
        assert canonical(as_admission(row).public()) == expected
    with migrated_engine.connect() as conn:
        after = conn.scalar(
            text("SELECT to_jsonb(t) FROM research_process_admissions t")
        )
    assert {
        k: v for k, v in after.items() if k not in {"schema_version", "ipc_policy"}
    } == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_version",
        "missing_policy",
        "v1_noipc",
        "v2_candidate",
        "v2_controller",
        "unknown_version",
        "null_ticket",
    ],
)
def test_real_versioned_direct_sql_cannot_bypass_closed_profile(
    migrated_engine, admission, mutation
):
    with migrated_engine.begin() as conn:
        row = dict(
            conn.execute(text("SELECT * FROM research_process_admissions"))
            .mappings()
            .one()
        )
        from uuid import uuid4

        row["id"] = uuid4()
        if mutation == "missing_version":
            row.pop("schema_version")
        elif mutation == "missing_policy":
            row.pop("ipc_policy")
        elif mutation == "v1_noipc":
            row["ipc_policy"] = "none"
        elif mutation == "null_ticket":
            row["ticket_sha256"] = None
        elif mutation == "unknown_version":
            row["schema_version"] = "unknown"
        else:
            row.update(
                schema_version=V2,
                profile="disabled.test.v2",
                role="factor_cutoff_candidate"
                if mutation == "v2_candidate"
                else "rd_orchestrator",
            )
            if mutation == "v2_candidate":
                row.update(ipc_policy="none", ticket_sha256=None)
        sql = (
            "INSERT INTO research_process_admissions ("
            + ",".join(row)
            + ") VALUES ("
            + ",".join(":" + k for k in row)
            + ")"
        )
        with pytest.raises(DBAPIError) as error, conn.begin_nested():
            conn.execute(text(sql), row)
        assert error.value.orig.sqlstate in {"23502", "23514"}


def test_real_versioned_columns_are_immutable(migrated_engine, admission):
    with (
        migrated_engine.begin() as conn,
        pytest.raises(DBAPIError, match="immutable"),
        conn.begin_nested(),
    ):
        conn.execute(text("UPDATE research_process_admissions SET ipc_policy='none'"))


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_real_versioned_migration_refuses_concurrent_readers(
    migrated_engine, direction, admission
):
    if direction == "upgrade":
        migrate(migrated_engine, "015_neutral_primitives", downgrade=True)
    with migrated_engine.begin() as reader:
        reader.execute(
            text("LOCK TABLE research_process_admissions IN ACCESS SHARE MODE")
        )
        before = reader.scalar(
            text("SELECT to_jsonb(t) FROM research_process_admissions t")
        )
        with pytest.raises(DBAPIError) as error:
            migrate(
                migrated_engine,
                "016_process_versioning"
                if direction == "upgrade"
                else "015_neutral_primitives",
                downgrade=direction == "downgrade",
            )
        assert error.value.orig.sqlstate == "55P03"
        assert (
            reader.scalar(text("SELECT to_jsonb(t) FROM research_process_admissions t"))
            == before
        )


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_real_versioned_migration_requires_exclusive_maintenance(
    migrated_engine, direction, admission
):
    if direction == "upgrade":
        migrate(migrated_engine, "015_neutral_primitives", downgrade=True)
    with migrated_engine.begin() as holder:
        assert (
            holder.scalar(
                text("SELECT pg_try_advisory_xact_lock_shared(1413960260,1129270340)")
            )
            is True
        )
        before = holder.scalar(
            text("SELECT to_jsonb(t) FROM research_process_admissions t")
        )
        with pytest.raises(DBAPIError, match="quiescent maintenance"):
            migrate(
                migrated_engine,
                "016_process_versioning"
                if direction == "upgrade"
                else "015_neutral_primitives",
                downgrade=direction == "downgrade",
            )
        assert (
            holder.scalar(text("SELECT to_jsonb(t) FROM research_process_admissions t"))
            == before
        )
