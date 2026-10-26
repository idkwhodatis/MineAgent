"""Offline exact-inventory reversal checks; no PostgreSQL roundtrip claim."""

import importlib.util
import re
from pathlib import Path

import pytest

from backend.app import rdagent_downgrade_sql as module
from backend.app.numeric_process_ddl import PROFILE_GATE
from backend.app.rdagent_backup import FOREIGN_KEYS

ROOT = Path(__file__).resolve().parents[2]


def migration():
    spec = importlib.util.spec_from_file_location(
        "_rd_downgrade_migration_fixture",
        ROOT / "backend/alembic/versions/018_rd_source_persistence.py",
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_exact_018_domain_order_and_external_unique_targets():
    current = migration()
    assert tuple(current.DOMAIN_TABLES) == module.DOMAIN_TABLES
    assert len(module.DOMAIN_TABLES) == 24
    for table, name, columns in module.EXTERNAL_UNIQUES:
        expected = (
            f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({', '.join(columns)});"
        )
        assert expected in current.UPGRADE_SQL


def test_drop_order_is_reverse_of_every_internal_fk_dependency():
    order = {name: n for n, name in enumerate(reversed(module.DOMAIN_TABLES))}
    for source, references in FOREIGN_KEYS.items():
        for _, target, _ in references:
            if target in order and target != source:
                assert order[source] < order[target], (source, target)


def test_every_018_function_is_removed_without_old_function_names():
    created = set(
        re.findall(
            r"CREATE (?:OR REPLACE )?FUNCTION\s+(?:@SCHEMA@\.)?(\w+)\s*\(",
            migration().UPGRADE_SQL,
        )
    )
    removed = {signature.split("(")[0] for signature in module.FUNCTIONS}
    assert created == removed
    assert all(
        name.startswith(("mineagent_rd_", "mineagent_engine_")) for name in removed
    )
    assert not any(name.startswith("mineagent_neutral_") for name in removed)
    assert "mineagent_broker_request" not in removed
    assert "mineagent_legacy_run_config" not in removed


def test_lock_inventory_precedes_refusal_and_any_destructive_statement():
    sql = module.render_sql("rd_test")
    assert sql.index("pg_try_advisory_xact_lock") < sql.index("LOCK TABLE")
    assert sql.index("LOCK TABLE") < sql.index("SET CONSTRAINTS ALL IMMEDIATE")
    assert sql.index("SET CONSTRAINTS ALL IMMEDIATE") < sql.index(
        "RD downgrade refuses"
    )
    assert sql.rindex("RD downgrade refuses") < sql.index("DROP TRIGGER")
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in sql
    assert "current_setting('transaction_isolation') <> 'read committed'" in sql
    for table in module.DOMAIN_TABLES:
        assert f'SELECT 1 FROM "rd_test"."{table}"' in sql
    assert "CASCADE;" not in sql
    assert "DELETE FROM" not in sql
    assert "TRUNCATE" not in sql


@pytest.mark.parametrize(
    ("table", "predicate"),
    [
        ("research_runs", "purpose IS DISTINCT FROM 'legacy_strategy'"),
        (
            "research_model_operations",
            "protocol IS DISTINCT FROM 'mineagent.neutral-model.v1'",
        ),
        (
            "research_operation_contexts",
            "context->>'schema_version' IS DISTINCT FROM 'mineagent.neutral-fixture-context.v1'",
        ),
        ("research_process_admissions", "role='rd_orchestrator'"),
        (
            "research_objects",
            "object_kind NOT IN ('model_request','model_response','process_receipt')",
        ),
        (
            "research_object_publications",
            "object_kind NOT IN ('model_request','model_response','process_receipt')",
        ),
        ("factor_versions", "source_kind='rdagent'"),
        (
            "engine_object_reservation_consumers",
            "consumer_kind IS DISTINCT FROM 'research_object'",
        ),
    ],
)
def test_extended_evidence_rejected_without_requiring_legacy_data_empty(
    table, predicate
):
    sql = module.render_sql()
    assert f'SELECT 1 FROM "public"."{table}" WHERE {predicate}' in sql
    assert f'IF EXISTS (SELECT 1 FROM "public"."{table}")' not in sql


def test_restores_017_numeric_and_old_legacy_checks_not_an_all_closed_stub():
    sql = module.render_sql()
    assert PROFILE_GATE in sql
    assert "DROP COLUMN protocol RESTRICT" in sql
    assert "ALTER COLUMN strategy_version_id SET NOT NULL" in sql
    assert 'ADD CHECK ("public".mineagent_broker_request(request))' in sql
    assert "mineagent_legacy_run_config(config) IS TRUE" in sql
    assert module.OLD_CONTEXT in sql
    for name in ("research_objects", "research_object_publications"):
        assert f"DROP CONSTRAINT ck_{name}_versioned_kind" in sql
    assert (
        "ADD CHECK (purpose IN ('model_transport','process_unit','object_storage'))"
        in sql
    )
    assert 'DROP FUNCTION "public".mineagent_neutral_' not in sql
    assert 'DROP FUNCTION "public".mineagent_numeric_' not in sql


def test_added_shared_triggers_removed_before_owned_tables():
    sql = module.render_sql()
    first_drop = sql.index("DROP TABLE")
    for table in module.SHARED_TABLES:
        for trigger in (
            "rd_source_semantics",
            "rd_shared_semantics",
            "a_rd_source_run_lock",
        ):
            needle = f'DROP TRIGGER IF EXISTS "{trigger}" ON "public"."{table}";'
            assert needle in sql[:first_drop]
    assert (
        'DROP TRIGGER engine_object_claim ON "public"."research_object_publications"'
        in sql[:first_drop]
    )
    assert (
        'DROP TRIGGER engine_object_consistent ON "public"."research_object_publications"'
        in sql[:first_drop]
    )


@pytest.mark.parametrize("schema", ["bad.name", "Bad", "x;DROP", "", "a" * 64])
def test_rejects_untrusted_schema(schema):
    with pytest.raises(ValueError):
        module.render_sql(schema)


def test_rejects_missing_reordered_or_added_domain_or_unique_inventory():
    for tables in (
        module.DOMAIN_TABLES[:-1],
        tuple(reversed(module.DOMAIN_TABLES)),
        module.DOMAIN_TABLES + ("research_runs",),
    ):
        with pytest.raises(ValueError):
            module.render_sql(domain_tables=tables)
    with pytest.raises(ValueError):
        module.render_sql(external_uniques=module.EXTERNAL_UNIQUES[:-1])
    assert module.render_sql() == module.render_sql()
