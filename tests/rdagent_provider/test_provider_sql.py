"""Offline fragment contracts, not PostgreSQL or SDK containment acceptance."""

import json
import re

from backend.app.rdagent_provider_sql import (
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_render_is_literal_additive_and_closed_schema():
    sql = render_upgrade_sql()
    assert sql == render_upgrade_sql()
    assert "@RULES@" not in sql
    assert "CREATE OR REPLACE" not in sql
    assert "DROP FUNCTION" not in sql
    assert "@SCHEMA@." in sql
    rules = re.findall(r"WHEN '(\w+)' THEN rule := '(.+)'::jsonb;", sql)
    assert {name for name, _ in rules} == {"config", "binding", "chat", "embedding"}
    for _, raw in rules:
        rule = json.loads(raw.replace("''", "'"))
        assert rule["additionalProperties"] is False
        assert set(rule["required"]) == set(rule["properties"])
    assert sql.count("CREATE CONSTRAINT TRIGGER rd_provider_graph ") == len(TABLES)


def test_nulls_never_grant_config_or_request_or_settlement():
    sql = render_upgrade_sql()
    for predicate in (
        "config_valid IS NOT TRUE",
        "run.config->'steer_snapshot'->>'owner_id' IS DISTINCT FROM op.owner_id::text",
        "op.protocol IS DISTINCT FROM op.request->>'schema_version'",
        "op.request_sha256 IS DISTINCT FROM",
        "held.state IS DISTINCT FROM 'settled'",
        "settlement.outcome IS DISTINCT FROM 'conservative_unknown'",
        "settlement.charge IS DISTINCT FROM amount",
        "outcome.usage IS DISTINCT FROM amount",
    ):
        assert predicate in sql


def test_reverse_membership_actor_and_historical_steer_not_current_head():
    sql = render_upgrade_sql()
    assert "WHERE operation_id=op.id)<>1" in sql
    assert "a.logical_operation_id=b.id" in sql
    assert "a.role='rd_orchestrator'" in sql
    assert "a.profile='mineagent.rdagent-orchestrator.v2'" in sql
    assert "FROM rd_steer_versions sv" in sql
    assert "sv.content_canonical::jsonb=selected" in sql
    assert "current_version_id" not in sql and "disabled" not in sql
    assert "at most 32 operations per run" in sql
    assert "IF n<>1" in sql and "cannot automatically retry" in sql


def test_sdk_references_require_publication_and_full_charge_not_physical_claim():
    sql = render_upgrade_sql()
    assert "ARRAY['intent','started','stopped']" in sql
    assert "JOIN research_object_outcomes done" in sql
    assert "JOIN research_object_publications pub" in sql
    assert "settled.charge=object_hold.amounts" in sql
    assert "w.size_bytes BETWEEN 1 AND 16384" in sql
    assert "pg_read" not in sql and "/proc/" not in sql
    assert "IF TG_OP<>'INSERT'" in sql
    assert "FOR UPDATE OF r NOWAIT" in sql


def test_downgrade_rejects_retained_provider_before_removing_guards():
    sql = render_downgrade_sql()
    assert sql.index("Provider evidence blocks downgrade") < sql.index("DROP TRIGGER")
    assert "research_model_operations WHERE protocol IN" in sql
    assert "CASCADE" not in sql
    assert sql.count("DROP TRIGGER rd_provider_graph ") == len(TABLES)


def test_attempt_and_run_terminal_mutations_recheck_all_provider_transport_evidence():
    sql = render_upgrade_sql()
    assert {"execution_attempts", "research_runs"} <= set(TABLES)
    assert "IF TG_TABLE_NAME='research_runs' THEN rid:=NEW.id;" in sql
    assert "FROM execution_attempts WHERE id=op.attempt_id" in sql
    assert (
        "(attempt.run_id,attempt.generation) IS DISTINCT FROM (op.run_id,op.generation)"
        in sql
    )
    assert "attempt.sandbox_stopped IS TRUE OR attempt.finished_at IS NOT NULL" in sql
    assert "attempt.status IN ('succeeded','failed','cancelled','interrupted')" in sql
    assert "run.status IN ('succeeded','failed','cancelled','interrupted')" in sql
    assert (
        "must_be_stopped AND (t.state IS NULL OR t.state NOT IN ('completed','failed','unknown'))"
        in sql
    )
    assert "IF (t.state='completed' OR must_be_stopped) AND (SELECT array_agg" in sql
    assert "Provider terminal usage cannot refund its whole held vector" in sql


def test_graph_reuses_only_function_local_pure_config_after_first_operation():
    sql = render_upgrade_sql()
    start = sql.index("CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_graph(rid uuid)")
    graph = sql[
        start : sql.index(
            "CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_graph_trigger", start
        )
    ]
    assert graph.count("mineagent_rd_provider_config(run.config)") == 1
    assert graph.index("at most 32 operations per run") < graph.index("FOR op IN")
    assert graph.index("FOR op IN") < graph.index("IF NOT config_checked THEN")
    assert graph.index("run.owner_id IS DISTINCT FROM op.owner_id") < graph.index(
        "IF NOT config_checked THEN"
    )
    assert "IF config_valid IS TRUE THEN" in graph
    assert "config_checked:=true;" in graph
    assert (
        "OR run.config->>'steer_snapshot_sha256' IS DISTINCT FROM steer_digest" in graph
    )
    assert graph.index("FROM rd_steer_versions sv") > graph.index(
        "config_checked:=true;"
    )
    assert (
        "FOR w IN SELECT * FROM rd_provider_transport_receipts WHERE run_id=rid LOOP"
        in graph
    )
