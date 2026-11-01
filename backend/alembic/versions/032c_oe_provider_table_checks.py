"""Add exact structured provider row CHECK branches; preserve historical SQL."""

import re

from alembic import op

revision = "032c_oe_provider_table_checks"
down_revision = "032b_oe_append_run_state"
branch_labels = None
depends_on = None

# fmt: off
UPGRADE_SQL = "ALTER TABLE @SCHEMA@.research_operation_contexts DROP CONSTRAINT ck_versioned_operation_context, ADD CONSTRAINT ck_versioned_operation_context CHECK ((((((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal']\n AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1'\n AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number'\n AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE\n OR @SCHEMA@.mineagent_rd_source_context(context) IS TRUE OR @SCHEMA@.mineagent_oe_context(context) IS TRUE))) OR @SCHEMA@.mineagent_oes_iteration_contract('context',context) IS TRUE));\nALTER TABLE @SCHEMA@.research_model_operations DROP CONSTRAINT research_model_operations_request_object_kind_check, ADD CONSTRAINT research_model_operations_request_object_kind_check CHECK ((CASE WHEN protocol='mineagent.oe-structured-provider-chat.v1' THEN (purpose='chat' AND ordinal=0 AND request_object_kind='oe_structured_provider_request') ELSE (request_object_kind = 'model_request') END) IS TRUE);\nALTER TABLE @SCHEMA@.research_model_outcomes DROP CONSTRAINT research_model_outcomes_check, ADD CONSTRAINT research_model_outcomes_check CHECK ((((((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE)) OR (outcome='completed' AND response_object_id IS NOT NULL AND response_object_kind='oe_structured_provider_response' AND response_sha256 ~ '^[0-9a-f]{64}$') IS TRUE));"

DOWNGRADE_SQL = "DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.research_operation_contexts WHERE ((((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal']\n AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1'\n AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number'\n AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE\n OR @SCHEMA@.mineagent_rd_source_context(context) IS TRUE OR @SCHEMA@.mineagent_oe_context(context) IS TRUE))) IS NOT TRUE) OR EXISTS(SELECT 1 FROM @SCHEMA@.research_model_operations WHERE ((request_object_kind = 'model_request')) IS NOT TRUE) OR EXISTS(SELECT 1 FROM @SCHEMA@.research_model_outcomes WHERE ((((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE)) IS NOT TRUE) THEN RAISE EXCEPTION 'Structured provider retained rows prevent downgrade'; END IF; END $$;\nALTER TABLE @SCHEMA@.research_operation_contexts DROP CONSTRAINT ck_versioned_operation_context, ADD CONSTRAINT ck_versioned_operation_context CHECK ((((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal']\n AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1'\n AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number'\n AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE\n OR @SCHEMA@.mineagent_rd_source_context(context) IS TRUE OR @SCHEMA@.mineagent_oe_context(context) IS TRUE)));\nALTER TABLE @SCHEMA@.research_model_operations DROP CONSTRAINT research_model_operations_request_object_kind_check, ADD CONSTRAINT research_model_operations_request_object_kind_check CHECK ((request_object_kind = 'model_request'));\nALTER TABLE @SCHEMA@.research_model_outcomes DROP CONSTRAINT research_model_outcomes_check, ADD CONSTRAINT research_model_outcomes_check CHECK ((((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE));"
# fmt: on


def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance(schema):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Contract repair migration requires quiescent maintenance'; END IF;
    END $$;""")
    tables = (
        "research_operation_contexts",
        "research_model_operations",
        "research_model_outcomes",
    )
    op.execute(
        "LOCK TABLE "
        + ",".join(schema + "." + name for name in tables)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")


def upgrade():
    schema = _schema()
    _maintenance(schema)
    op.get_bind().exec_driver_sql(
        UPGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )


def downgrade():
    schema = _schema()
    _maintenance(schema)
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
