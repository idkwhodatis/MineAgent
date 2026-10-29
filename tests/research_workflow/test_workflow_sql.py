"""Literal/metadata checks only. Actual PostgreSQL deferred/race gate is separate."""

import re

import pytest

from backend.app import (  # noqa: F401
    rdagent_steer_models,
    rdagent_workflow_models,
    research_input_models,
    research_workflow_models,
)
from backend.app.models import Base
from backend.app.research_workflow_sql import (
    CONTRACTS,
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_literal_closed_typed_schema_and_all_owned_tables():
    sql = render_upgrade_sql()
    assert "@RULES@" not in sql
    assert len(CONTRACTS) == 15
    for kind in CONTRACTS:
        assert f"WHEN '{kind}' THEN rule :=" in sql
    assert len(TABLES) == 6
    for table in TABLES:
        assert table in Base.metadata.tables
        assert f"BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table}" in sql
        assert (
            f"AFTER INSERT OR UPDATE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED"
            in sql
        )
    assert "pg_try_advisory_xact_lock" in sql and "FOR UPDATE NOWAIT" in sql
    assert "NEW.revision<>OLD.revision+1" in sql
    assert "Retained workflow child rows are immutable" in sql


def test_literal_table_references_exist_in_actual_metadata():
    sql = render_upgrade_sql()
    references = set(
        re.findall(r"(?:FROM|JOIN|ON @SCHEMA@\.)\s*([a-z][a-z0-9_]*)", sql)
    )
    tables = {
        name
        for name in references
        if name.startswith(("research_", "factor_", "rd_", "data_usage_", "execution_"))
    }
    assert tables <= set(Base.metadata.tables)


@pytest.mark.parametrize(
    "evidence",
    [
        "terminal_event_sha256",
        "controller_stop_sha256",
        "process_admission_ids",
        "reservation_ids",
        "conservative_unknown",
        "ARRAY['intent','started','stopped']",
        "predecessor_checkpoint_id",
        "rd_checkpoint_id",
        "exposure_ids",
        "engine_direct_analysis_release",
        "member.request_ordinal=0",
        "maximum_iterations",
    ],
)
def test_literal_evidence_is_not_omitted(evidence):
    assert evidence in render_upgrade_sql()


def test_downgrade_refuses_retained_roots():
    sql = render_downgrade_sql()
    assert "Retained workflows block downgrade" in sql
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql
    assert "DROP FUNCTION @SCHEMA@.mineagent_workflow_lock(uuid,uuid)" in sql
