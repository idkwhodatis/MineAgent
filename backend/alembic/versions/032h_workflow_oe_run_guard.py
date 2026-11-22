"""Admit exact workflow-origin v2 OE rows at the existing run CHECK.

031 installed the v2 contract and deferred attachment graph, but did not
extend 028's table CHECK (which excludes v2 before those guards can run).
Retain every old arm unchanged; the new arm still requires the full live
v2 contract along with exact row/config and workflow-context identities.
"""

import re

from alembic import op

revision = "032h_workflow_oe_run_guard"
down_revision = "032g_schema_keyset_cost"
branch_labels = None
depends_on = None

# Reviewed 028 UPGRADE_SQL literal, replayed unchanged on downgrade.
# fmt: off
PREDECESSOR_SQL_SHA256 = 'dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9'
ORIGINAL_CHECK = "(((((((purpose='legacy_strategy' AND engine='legacy' AND strategy_version_id IS NOT NULL\n AND @SCHEMA@.mineagent_legacy_run_config(config) IS TRUE) OR\n (purpose='factor_research' AND engine='rdagent' AND strategy_version_id IS NULL\n AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL\n AND submission_binding IS NULL AND portable_policy_image_digest IS NULL\n AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL\n AND @SCHEMA@.mineagent_rd_source_config(config) IS TRUE\n AND dataset_version_id::text=config->'input'->'dataset'->>'dataset_version_id'\n AND request_sha256=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(config,true),'UTF8')),'hex')\n AND (research_image_digest IS NULL OR research_image_digest=config->'runtime'->>'orchestrator_image')) OR\n (purpose='strategy_search' AND engine='openevolve' AND strategy_version_id IS NOT NULL\n AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL\n AND submission_binding IS NULL AND portable_policy_image_digest IS NULL\n AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL\n AND @SCHEMA@.mineagent_oe_source_config(config) IS TRUE\n AND owner_id::text=config->>'owner_id' AND id::text=config->>'run_id'\n AND strategy_version_id::text=config#>>'{preparation,prepared,strategy_version_id}'\n AND dataset_version_id::text=config#>>'{execution,dataset_version_id}'\n AND request_sha256=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(config,false),'UTF8')),'hex')\n AND (research_image_digest IS NULL OR research_image_digest=config->>'runtime_image'))) IS TRUE) OR (purpose='factor_strategy' AND engine='structured'\n AND strategy_version_id IS NULL AND generation=1\n AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL\n AND submission_binding IS NULL AND portable_policy_image_digest IS NULL\n AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL\n AND @SCHEMA@.mineagent_structured_contract('config',config) IS TRUE\n AND dataset_version_id::text=config->>'dataset_version_id'\n AND research_image_digest=config->>'engine_image_id')) IS TRUE) OR (purpose='strategy_search' AND engine='openevolve' AND strategy_version_id IS NULL AND generation=1\n AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL\n AND submission_binding IS NULL AND portable_policy_image_digest IS NULL\n AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL\n AND config->>'schema_version'='mineagent.oe-structured-provider-run.v1'\n AND @SCHEMA@.mineagent_oes_contract('config',config) IS TRUE\n AND dataset_version_id::text=config->'fixed_inputs'->>'dataset_version_id'\n AND research_image_digest=config->'runtime'->>'controller_image_id')) IS TRUE)"
WORKFLOW_V2_BRANCH = "(purpose='strategy_search' AND engine='openevolve' AND strategy_version_id IS NULL AND generation=1\n AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL\n AND submission_binding IS NULL AND portable_policy_image_digest IS NULL\n AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL\n AND config->>'schema_version'='mineagent.oe-structured-provider-run.v2'\n AND owner_id::text=config#>>'{search,owner_id}'\n AND id::text=config#>>'{search,run_id}'\n AND owner_id::text=config#>>'{workflow_context,inputs,binding,owner_id}'\n AND id::text=config#>>'{workflow_context,child_run_id}'\n AND @SCHEMA@.mineagent_oes_contract('config',config) IS TRUE\n AND dataset_version_id::text=config->'fixed_inputs'->>'dataset_version_id'\n AND research_image_digest=config->'runtime'->>'controller_image_id')"
WORKFLOW_CHECK = f"(({ORIGINAL_CHECK}) OR {WORKFLOW_V2_BRANCH}) IS TRUE"
# fmt: on


def _apply(expected_revision, before, after):
    connection = op.get_bind()
    schema = connection.exec_driver_sql(
        "SELECT pg_catalog.current_schema()"
    ).scalar_one()
    if type(schema) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise RuntimeError("Exact workflow OE guard installation schema required")
    quoted = '"' + schema + '"'
    versions = (
        connection.exec_driver_sql(f"SELECT version_num FROM {quoted}.alembic_version")
        .scalars()
        .all()
    )
    if versions != [expected_revision]:
        raise RuntimeError("Exact workflow OE guard predecessor revision required")
    path = connection.exec_driver_sql("SHOW search_path").scalar_one()
    connection.exec_driver_sql("SET LOCAL search_path = pg_catalog, pg_temp")
    try:
        connection.exec_driver_sql(
            migration_sql(quoted, before, after),
            execution_options={"no_parameters": True},
        )
    finally:
        # A failed backend transaction is rolled back by Alembic. Do not mask
        # a useful SQLSTATE by issuing a second command on the aborted session.
        if connection.connection.driver_connection.info.transaction_status != 3:
            connection.exec_driver_sql(
                "SELECT pg_catalog.set_config('search_path',%s,true)", (path,)
            )


def migration_sql(quoted, before, after):
    """Use PostgreSQL itself to canonicalize the known predecessor expression."""
    run_table = f"{quoted}.research_runs"
    copy = "pg_temp.mineagent_032h_expected_run_check"
    name = "ck_research_run_legacy_config"
    before, after = (value.replace("@SCHEMA@", quoted) for value in (before, after))
    return f"""
DO $$ BEGIN
    IF pg_catalog.current_setting('transaction_isolation') <> 'read committed'
       OR NOT pg_catalog.pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Workflow OE run guard requires quiescent maintenance'; END IF;
END $$;
LOCK TABLE {run_table} IN ACCESS EXCLUSIVE MODE NOWAIT;
CREATE TEMP TABLE {copy} (LIKE {run_table}) ON COMMIT DROP;
ALTER TABLE {copy} ADD CONSTRAINT {name} CHECK ({before});
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint c
        JOIN pg_catalog.pg_constraint e ON e.conrelid='{copy}'::pg_catalog.regclass
         AND e.conname='{name}'
        WHERE c.conrelid='{run_table}'::pg_catalog.regclass AND c.conname='{name}'
          AND c.contype='c' AND c.convalidated AND c.conenforced AND c.conislocal
          AND c.coninhcount=0 AND NOT c.connoinherit
          AND NOT c.condeferrable AND NOT c.condeferred
          AND pg_catalog.pg_get_constraintdef(c.oid) =
              pg_catalog.pg_get_constraintdef(e.oid)
    ) THEN RAISE EXCEPTION 'Exact workflow OE run guard predecessor CHECK required'; END IF;
END $$;
DROP TABLE {copy};
ALTER TABLE {run_table}
  DROP CONSTRAINT {name},
  ADD CONSTRAINT {name} CHECK ({after});
"""


def upgrade():
    _apply(down_revision, ORIGINAL_CHECK, WORKFLOW_CHECK)


def downgrade():
    # Revalidates every retained run under the exact old CHECK. Any v2 run
    # prevents downgrade, with the original constraints/rows preserved.
    _apply(revision, WORKFLOW_CHECK, ORIGINAL_CHECK)
