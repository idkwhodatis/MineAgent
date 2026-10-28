"""Actual PostgreSQL CHECK/repair matrix, not a fabricated successful seed graph.

The ordinary provider user-flow exercises the complete seed/parent graph. These
cases independently pin the formerly contradictory initial-step CHECK and its
fail-closed downgrade, while leaving all real FK/deferred constraints installed.
"""

import pytest
from sqlalchemy import text

from backend.app.seed_parent_contract_repair import DOWNGRADE_SQL, UPGRADE_SQL

pytestmark = pytest.mark.postgres


def test_installed_seed_parent_check_accepts_nonnull_only_at_initial_step(
    migrated_engine,
):
    with migrated_engine.begin() as connection:
        connection.execute(
            text("""
DO $$ DECLARE predicate text; BEGIN
 SELECT pg_get_expr(conbin,conrelid) INTO STRICT predicate
 FROM pg_constraint WHERE conrelid='factor_generation_starts'::regclass
 AND conname='ck_rd_source_initial_step';
 EXECUTE 'CREATE TEMP TABLE seed_parent_check_probe (logical_step integer NOT NULL,
 parent_factor_version_id uuid, CHECK ('||predicate||')) ON COMMIT DROP';
 INSERT INTO seed_parent_check_probe VALUES (0,NULL),
 (0,'00000000-0000-0000-0000-000000000001');
 BEGIN
 INSERT INTO seed_parent_check_probe VALUES(1,NULL);
 RAISE EXCEPTION 'Noninitial step incorrectly accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN
 INSERT INTO seed_parent_check_probe VALUES(-1,'00000000-0000-0000-0000-000000000001');
 RAISE EXCEPTION 'Negative step incorrectly accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='factor_generation_starts'::regclass
 AND tgname='rd_seed_closed' AND tgdeferrable AND tginitdeferred AND tgenabled='O')
 THEN RAISE EXCEPTION 'Prior seed receipt deferred guard missing'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='factor_generation_starts'::regclass
 AND contype='f' AND confrelid='factor_versions'::regclass)
 THEN RAISE EXCEPTION 'Parent foreign key missing'; END IF;
END $$;
""")
        )


def test_repair_downgrade_refuses_retained_parent_and_restores_empty_contract(
    migrated_engine,
):
    with migrated_engine.begin() as connection:
        connection.execute(
            text("""
CREATE TEMP TABLE factor_generation_starts (
 logical_step integer NOT NULL,parent_factor_version_id uuid,
 CONSTRAINT ck_rd_source_initial_step CHECK (logical_step=0 AND parent_factor_version_id IS NULL)
) ON COMMIT DROP;
""")
        )
        connection.execute(text(UPGRADE_SQL.replace("@SCHEMA@", "pg_temp")))
        connection.execute(
            text(
                "INSERT INTO pg_temp.factor_generation_starts VALUES(0,'00000000-0000-0000-0000-000000000001')"
            )
        )
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError) as refusal, connection.begin_nested():
            connection.execute(text(DOWNGRADE_SQL.replace("@SCHEMA@", "pg_temp")))
        assert refusal.value.orig.sqlstate == "P0001"
        assert refusal.value.orig.diag.message_primary == (
            "Seed parent contract downgrade refused: retained parent history"
        )
        connection.execute(text("DELETE FROM pg_temp.factor_generation_starts"))
        connection.execute(text(DOWNGRADE_SQL.replace("@SCHEMA@", "pg_temp")))
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO pg_temp.factor_generation_starts VALUES(0,'00000000-0000-0000-0000-000000000001')"
                )
            )
        assert error.value.orig.sqlstate == "23514"


def test_installed_authoring_contract_matches_real_canonical_producer(migrated_engine):
    from tests.integration.structured_authoring_fixture import authoring_statements

    with migrated_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        import re

        assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema)
        for statement in authoring_statements():
            connection.execute(text(statement.replace("@SCHEMA@", '"' + schema + '"')))
