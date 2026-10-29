"""Dependency inventory and literal checks, not PostgreSQL trigger acceptance."""

from backend.app import oe_structured_trigger_sql as triggers
from backend.app.oe_structured_checkpoint_sql import TABLES as CHECKPOINT
from backend.app.oe_structured_provider_sql import TABLES as PROVIDER


def test_all_new_models_have_append_fence_and_deferred_reverse_guard():
    from backend.app import (
        oe_structured_checkpoint_models,
        oe_structured_models,
        oe_structured_operational_models,
        oe_structured_sampling_models,
        oe_structured_terminal_models,
    )

    models = set()
    for module in (
        oe_structured_checkpoint_models,
        oe_structured_models,
        oe_structured_operational_models,
        oe_structured_sampling_models,
        oe_structured_terminal_models,
    ):
        models.update(
            value.__tablename__
            for value in vars(module).values()
            if isinstance(value, type)
            and getattr(value, "__tablename__", "").startswith("oe_structured_")
        )
    assert set(triggers.TABLES) == models
    sql = triggers.render_upgrade_sql()
    for table in models:
        assert f"BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table}" in sql
        assert f"AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table}" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "TG_OP<>'INSERT'" in sql
    assert "a.lease_expires_at<=clock_timestamp()" in sql
    assert "FOR UPDATE NOWAIT" in sql


def test_shared_mutations_recheck_original_and_new_dependency_sides():
    installed = set(triggers.TABLES + triggers.SHARED)
    assert set(PROVIDER + CHECKPOINT) <= installed
    assert {
        "structured_strategy_versions",
        "factor_execution_snapshot_outcomes",
        "dataset_versions",
    } <= installed
    sql = triggers.render_upgrade_sql()
    assert "ARRAY[to_jsonb(OLD),to_jsonb(NEW)]" in sql
    assert "ORDER BY run_id LIMIT 10001" in sql
    assert "cardinality(ids)>10000" in sql
    assert "versions:=(CASE" in sql
    # This new branch does not add isolation restrictions to unrelated old runs.
    tail = sql.split("CREATE FUNCTION @SCHEMA@.mineagent_oes_consistent()", 1)[1]
    assert tail.index("THEN CONTINUE; END IF;") < tail.index(
        "current_setting('transaction_isolation')"
    )


def test_declaration_and_runtime_insert_fences_are_distinct_and_closed():
    sql = triggers.render_upgrade_sql()
    assert (
        "r.status IS DISTINCT FROM 'queued' OR r.active_attempt_id IS NOT NULL" in sql
    )
    assert "r.status IS DISTINCT FROM 'running' OR r.finished_at IS NOT NULL" in sql
    assert (
        "r.config->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v1'"
        in sql
    )
    assert "a.generation IS DISTINCT FROM r.generation" in sql
    down = triggers.render_downgrade_sql()
    assert down.count("DROP TRIGGER oes027_consistent") == len(
        triggers.TABLES + triggers.SHARED
    )
    assert down.count("DROP TRIGGER oes027_append") == len(triggers.TABLES)
    assert down.index("DROP TRIGGER") < down.index("DROP FUNCTION")
