"""Current-head isolated OE candidate. Requires real PostgreSQL, never native proof."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import row_validation_oe_032g_sql as candidate
from tests.integration.test_row_validation_invariants import catalog, execute_sql

pytestmark = pytest.mark.postgres


def test_oe_only_current_head_catalog_roundtrip(migrated_engine):
    with migrated_engine.connect() as connection, connection.begin():
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "032g_schema_keyset_cost"
        )
        before = catalog(connection)
        execute_sql(connection, candidate.render_candidate_sql())
        after = catalog(connection)
        changed = set(candidate.CHANGED)
        assert [row for row in before[0] if row[0] not in changed] == [
            row for row in after[0] if row[0] not in changed
        ]
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "032g_schema_keyset_cost"
        )
        execute_sql(connection, candidate.render_candidate_sql("downgrade"))
        assert catalog(connection) == before


def test_oe_candidate_rejects_repeated_install(migrated_engine):
    with migrated_engine.connect() as connection, connection.begin():
        execute_sql(connection, candidate.render_candidate_sql())
        with pytest.raises(DBAPIError), connection.begin_nested():
            execute_sql(connection, candidate.render_candidate_sql())
        execute_sql(connection, candidate.render_candidate_sql("downgrade"))


@pytest.mark.parametrize("argument", [None, "00000000-0000-0000-0000-00003ade68b1"])
def test_missing_oe_roots_preserve_original_rejection(migrated_engine, argument):
    from tests.integration.test_row_validation_invariants import call_outcome

    with migrated_engine.connect() as c, c.begin():
        before = {name: call_outcome(c, name, argument) for name in candidate.CHANGED}
        execute_sql(c, candidate.render_candidate_sql())
        assert {
            name: call_outcome(c, name, argument) for name in candidate.CHANGED
        } == before


@pytest.mark.parametrize(
    "blocker",
    ["advisory", "table", "isolation", "helper", "default", "scope", "append"],
)
def test_oe_preflight_failure_preserves_catalog_atomically(migrated_engine, blocker):
    from backend.app import row_validation_oe_032g_base_sql as base

    with migrated_engine.connect() as held, held.begin():
        if blocker == "advisory":
            held.exec_driver_sql("SELECT pg_advisory_xact_lock(1413960260,1129270340)")
        elif blocker == "table":
            held.exec_driver_sql("LOCK TABLE oe_structured_specs IN ROW EXCLUSIVE MODE")
        with migrated_engine.connect() as c:
            if blocker == "isolation":
                c = c.execution_options(isolation_level="REPEATABLE READ")
            with c.begin():
                if blocker == "helper":
                    execute_sql(
                        c,
                        base.original("mineagent_structured_raw_json").replace(
                            "BEGIN\n", "BEGIN\n -- unreviewed drift\n", 1
                        ),
                    )
                elif blocker == "default":
                    execute_sql(
                        c,
                        base.original("mineagent_rd_schema").replace(
                            "DEFAULT 0", "DEFAULT 1", 1
                        ),
                    )
                elif blocker == "scope":
                    table, name = next(iter(candidate.scope_foreign_keys().items()))
                    c.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
                elif blocker == "append":
                    c.exec_driver_sql(
                        "ALTER TABLE oe_structured_specs DISABLE TRIGGER USER"
                    )
                before = catalog(c)
                with pytest.raises(DBAPIError) as caught, c.begin_nested():
                    execute_sql(c, candidate.render_candidate_sql())
                assert caught.value.orig.sqlstate == (
                    "55P03" if blocker == "table" else "P0001"
                )
                assert catalog(c) == before
                c.rollback()


@pytest.fixture
def current_content_db(migrated_engine):
    """Synthetic component rows use actual032g functions, never native evidence."""
    with migrated_engine.begin() as c:
        for table, columns in candidate.REQUIRED_COLUMNS.items():
            cols = ",".join(
                column
                + (" jsonb" if column == "strategy_input" else " text")
                + " NOT NULL"
                for column in columns
            )
            c.exec_driver_sql(
                f"CREATE TABLE rvi_content_{table}(id integer PRIMARY KEY,{cols})"
            )
    return migrated_engine


def test_current_content_corruption_and_savepoint(current_content_db):
    # Reuse reviewed corpus/mutations; predicates are identical, installed pure
    # closure is now the genuine032g schema rather than the old component fixture.
    from tests.integration.test_row_validation_content import (
        test_component_all_content_checks_valid_negative_and_savepoint,
    )

    test_component_all_content_checks_valid_negative_and_savepoint(current_content_db)


@pytest.mark.parametrize("invariant", range(10))
def test_current_content_stale_checks_require_fresh_scan(current_content_db, invariant):
    from tests.integration.test_row_validation_content import (
        test_component_each_stale_content_check_fails_fresh_scan_atomically,
    )

    test_component_each_stale_content_check_fails_fresh_scan_atomically(
        current_content_db, invariant
    )


def test_current_epoch_rejects_relational_schema_union(current_content_db):
    from tests.integration.test_row_validation_content import (
        test_component_epoch_counterexample_keeps_spec_selected_schema,
    )

    test_component_epoch_counterexample_keeps_spec_selected_schema(current_content_db)


def test_current_content_read_savings_and_write_cost(current_content_db, capsys):
    """Component costs only: not a completed graph or SDK deadline result."""
    from tests.integration.test_row_validation_content import (
        test_component_extended_nonconstant_cost_and_write_overhead,
    )

    test_component_extended_nonconstant_cost_and_write_overhead(
        current_content_db, capsys
    )
