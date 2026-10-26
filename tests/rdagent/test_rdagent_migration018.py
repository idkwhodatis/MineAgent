"""Offline frozen migration composition checks, not PostgreSQL execution evidence."""

import ast
import re
from pathlib import Path

import pytest

from backend.app import (
    rdagent_boundary_models,
    rdagent_input_models,
    rdagent_models,
    rdagent_source_witness_models,
)
from backend.app.engine_evidence_ddl import materialize
from backend.app.rdagent_canonical_sql import render_sql as canonical
from backend.app.rdagent_downgrade_sql import DOMAIN_TABLES
from backend.app.rdagent_downgrade_sql import render_sql as downgrade
from backend.app.rdagent_identity_sql import render_sql as identity
from backend.app.rdagent_persistence_ddl import (
    CLOSED_OUTCOME_TABLES,
    RUN_CONFIG,
    RUN_IDENTITY,
    closed_phase_sql,
    shared_upgrade_sql,
)
from backend.app.rdagent_shared_semantics_sql import render_sql as shared
from backend.app.rdagent_shared_semantics_sql import run_lock_sql
from backend.app.rdagent_source_sql import render_sql as source

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "backend/alembic/versions/018_rd_source_persistence.py"


def literals():
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(PATH.read_text()).body
        if isinstance(node, ast.Assign)
    }


def substitute(sql):
    return (
        sql.replace('"public".', "@SCHEMA@.")
        .replace("public.", "@SCHEMA@.")
        .replace(
            "search_path = public, pg_catalog", "search_path = @SCHEMA@, pg_catalog"
        )
    )


def domain():
    modules = (
        rdagent_models,
        rdagent_source_witness_models,
        rdagent_boundary_models,
        rdagent_input_models,
    )
    names = {
        value.__table__.name
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and hasattr(value, "__table__")
    }
    return materialize(rdagent_models.Base.metadata, names)


def test_frozen_revision_and_exact_domain_inventory():
    fixed = literals()
    assert fixed["revision"] == "018_rd_source_persistence"
    assert fixed["down_revision"] == "017_numeric_process_boundary"
    assert fixed["branch_labels"] is None and fixed["depends_on"] is None
    assert fixed["DOMAIN_TABLES"] == DOMAIN_TABLES
    assert len(DOMAIN_TABLES) == 24
    assert tuple(domain()["domain_tables"]) == DOMAIN_TABLES
    created = set(
        re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(?:@SCHEMA@\.)?(\w+)\s*\(", fixed["UPGRADE_SQL"])
    )
    assert created == set(DOMAIN_TABLES) | {"engine_object_reservation_consumers"}


def test_upgrade_equals_current_exact_renderer_composition():
    rendered = domain()["sql"].replace(
        "consumer_kind text NOT NULL CHECK",
        "consumer_kind text NOT NULL CONSTRAINT "
        "engine_object_reservation_consumers_consumer_kind_check CHECK",
    )
    expected = substitute(
        "\n".join(
            [
                canonical(),
                identity(),
                shared_upgrade_sql(),
                rendered,
                run_lock_sql(DOMAIN_TABLES),
                shared(),
                source(),
                closed_phase_sql(),
            ]
        )
    ).replace(
        "LANGUAGE plpgsql AS $$",
        "LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$",
    )
    assert literals()["UPGRADE_SQL"] == expected


def test_downgrade_equals_current_exact_renderer_composition():
    assert literals()["DOWNGRADE_SQL"] == substitute(downgrade())


@pytest.mark.parametrize("table", CLOSED_OUTCOME_TABLES)
def test_source_only_phase_is_closed_before_insert(table):
    sql = literals()["UPGRADE_SQL"]
    assert (
        f"CREATE TRIGGER rd_source_phase_closed BEFORE INSERT ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION mineagent_rd_source_only_phase();"
    ) in sql
    assert (
        "RAISE EXCEPTION 'RD finalization/adoption/resume phase is not admitted" in sql
    )


def test_identity_protocol_branches_are_explicit_not_wildcards():
    sql = literals()["UPGRADE_SQL"]
    assert f"CHECK ({RUN_IDENTITY})" in sql
    assert f"CHECK ({RUN_CONFIG})" in sql
    assert "protocol varchar(60) NOT NULL DEFAULT 'mineagent.neutral-model.v1'" in sql
    assert (
        "protocol='mineagent.neutral-model.v1' AND mineagent_broker_request(request) IS TRUE"
        in sql
    )
    assert (
        "protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1') AND mineagent_rd_source_request(request,protocol) IS TRUE"
        in sql
    )
    assert (
        "schema_version='mineagent.process-admission.v2' AND role='rd_orchestrator'"
        in sql
    )
    assert (
        "profile='mineagent.rdagent-orchestrator.v1' AND ipc_policy='host_ticket'"
        in sql
    )
    assert (
        "object_kind IN ('model_request','model_response','process_receipt','factor_response_assembly')"
        in sql
    )


def test_all_exact_run_lock_participants_and_nowait():
    sql = literals()["UPGRADE_SQL"]
    actual = set(
        re.findall(
            r"CREATE TRIGGER a_rd_source_run_lock BEFORE INSERT OR UPDATE OR DELETE ON (\w+)",
            sql,
        )
    )
    expected = set(DOMAIN_TABLES) | {
        "research_runs",
        "execution_attempts",
        "research_budget_controls",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_operation_contexts",
        "research_context_aborts",
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
        "research_object_publications",
        "research_process_admissions",
        "research_process_receipts",
        "research_process_states",
        "engine_object_reservation_consumers",
    }
    assert actual == expected
    assert "WHERE id=run_uuid FOR UPDATE NOWAIT" in sql
    assert "IF actual_engine='rdagent' THEN" in sql


def test_old_validator_functions_are_neither_replaced_nor_dropped():
    old_path = next((ROOT / "backend/alembic/versions").glob("015_*.py"))
    old = old_path.read_text()
    old_names = set(re.findall(r"CREATE (?:OR REPLACE )?FUNCTION\s+(\w+)\s*\(", old))
    assert "mineagent_broker_request" in old_names
    fixed = literals()
    modified = set(
        re.findall(
            r"(?:CREATE (?:OR REPLACE )?|DROP )FUNCTION(?: IF EXISTS)?\s+(?:@SCHEMA@\.)?(\w+)\s*\(",
            fixed["UPGRADE_SQL"] + "\n" + fixed["DOWNGRADE_SQL"],
        )
    )
    assert not old_names & modified
    assert all(
        name.startswith(("mineagent_rd_", "mineagent_engine_")) for name in modified
    )


def test_static_migration_has_no_runtime_renderer_or_model_import():
    tree = ast.parse(PATH.read_text())
    modules = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert modules == ["alembic"]
    assert 'UPGRADE_SQL.replace("@SCHEMA@",schema)' in PATH.read_text()
    assert 'DOWNGRADE_SQL.replace("@SCHEMA@", schema)' in PATH.read_text()
    assert "pg_try_advisory_xact_lock(1413960260, 1129270340)" in PATH.read_text()
