"""Offline DDL parity only; real PostgreSQL tests are separately collected."""

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.structured_factor_ddl import TABLE_SQL, TABLES
from backend.app.structured_factor_models import Base
from backend.app.structured_factor_raw_sql import RAW_SQL
from backend.app.structured_factor_sql import (
    GRAPH_SQL,
    TRIGGER_SQL,
    render_contract_sql,
)


def test_frozen_table_ddl_matches_exact_owned_models():
    expected = (
        "\n;\n".join(
            str(
                CreateTable(Base.metadata.tables[name]).compile(
                    dialect=postgresql.dialect()
                )
            )
            for name in TABLES
        )
        + "\n;\n"
    )
    assert TABLE_SQL == expected
    assert len(TABLES) == 7


def test_every_foreign_key_targets_declared_exact_unique_tuple():
    for name in TABLES:
        for fk in Base.metadata.tables[name].foreign_key_constraints:
            target = fk.elements[0].column.table
            cols = tuple(e.column.name for e in fk.elements)
            keys = [tuple(c.name for c in target.primary_key.columns)] + [
                tuple(c.columns.keys())
                for c in target.constraints
                if isinstance(c, UniqueConstraint)
            ]
            assert cols in keys, (name, cols)


def test_raw_float_codec_is_explicit_successor_not_historical_replacement():
    assert "CREATE OR REPLACE" not in RAW_SQL
    assert "16777216" in RAW_SQL
    assert "string_agg" in RAW_SQL
    assert "token:=v::text" in RAW_SQL
    assert "mineagent_structured_raw_json" in RAW_SQL
    assert "mineagent_analysis_raw_json" not in RAW_SQL


def test_all_closed_contracts_are_registered_with_serialized_defaults():
    sql = render_contract_sql()
    assert all(
        "WHEN '" + name + "'" in sql
        for name in ("program", "config", "input", "result", "decision")
    )
    assert "closed_operator_trusted_engine" in sql
    assert "hold_without_factor_read" in sql
    assert "additionalProperties" in sql


def test_raw_result_binds_full_receipt_and_actual_record_inventories():
    assert "mineagent_structured_stopped(a.id,true)" in GRAPH_SQL
    assert "o.result_canonical::json" in GRAPH_SQL
    assert "ex.payload->>'sha256'" in GRAPH_SQL
    assert (
        "factor_strategy_orders" in GRAPH_SQL and "factor_strategy_fills" in GRAPH_SQL
    )
    assert "bs.charge=b.amounts AND bs.outcome='conservative_unknown'" in GRAPH_SQL
    assert "mineagent_execution_snapshot_check(e.snapshot_id)" in GRAPH_SQL
    assert "'logical_order_ids'" in GRAPH_SQL


def test_current_run_before_insert_and_reverse_orphan_checks_remain_closed():
    assert "FOR UPDATE NOWAIT" in TRIGGER_SQL
    assert "a.lease_expires_at IS NULL OR a.lease_expires_at<=clock_timestamp()" in TRIGGER_SQL
    assert "TG_OP<>'INSERT'" in TRIGGER_SQL
    assert "Structured process cannot bypass exact input declaration" in TRIGGER_SQL
    assert "Structured source exposure cannot be orphaned" in TRIGGER_SQL
    assert "a.launch_ordinal" in TRIGGER_SQL
