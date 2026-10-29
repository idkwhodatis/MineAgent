"""Renderer contracts only; actual installed predicate gate remains required."""

import json
import re

from backend.app.numeric_rejection_sql import (
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_additive_exact_four_journal_grammars():
    sql = render_upgrade_sql()
    assert sql == render_upgrade_sql()
    assert "@RULES@" not in sql and "@LIMITS@" not in sql
    assert "CREATE OR REPLACE" not in sql
    rules = re.findall(r"WHEN '(\w+)' THEN rule := '(.+)'::jsonb;", sql)
    assert {name for name, _ in rules} == {
        "launch_intent",
        "launch_identity",
        "stop",
        "rejection",
    }
    for _, raw in rules:
        schema = json.loads(raw.replace("''", "'"))
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    assert sql.count("CREATE CONSTRAINT TRIGGER numeric_rejection_graph ") == len(
        TABLES
    )


def test_exact_containment_charge_and_raw_payload_not_missing_output_heuristic():
    sql = render_upgrade_sql()
    for value in (
        "n<>4",
        "factor_cutoff_candidate",
        "b.charge=r.amounts",
        "b.outcome='conservative_unknown'",
        "deadline_bound_met' IS DISTINCT FROM 'true'",
        "rejection->>'stop_sha256' IS DISTINCT FROM sh",
        "rejection->>'payload_utf8' IS DISTINCT FROM raw",
        "intent->'admission' IS DISTINCT FROM decl",
        "interval '10 seconds'",
        "interval '1 second'",
    ):
        assert value in sql
    assert "pg_read" not in sql and "/proc/" not in sql


def test_downgrade_blocks_before_dropping_helpers():
    sql = render_downgrade_sql()
    assert sql.index("Retained numeric rejection blocks downgrade") < sql.index(
        "DROP TRIGGER"
    )
    assert "CASCADE" not in sql
    assert sql.count("DROP TRIGGER numeric_rejection_graph ") == len(TABLES)
