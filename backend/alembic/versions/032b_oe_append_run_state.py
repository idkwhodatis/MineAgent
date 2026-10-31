"""Repair OE append fencing without inventing a run terminal timestamp.

Additive maintenance only; historical001-through032a literals are immutable.
"""

import re

from alembic import op

revision = "032b_oe_append_run_state"
down_revision = "032a_rd_terminal_results"
branch_labels = None
depends_on = None

# fmt: off
UPGRADE_SQL = "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_oes_append() RETURNS trigger\nLANGUAGE plpgsql SET search_path=pg_catalog AS $$\nDECLARE v jsonb; r record; a record;\nBEGIN\n IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Structured search evidence is immutable'; END IF;\n IF current_setting('transaction_isolation')<>'read committed'\n THEN RAISE EXCEPTION 'Structured search READ COMMITTED required'; END IF;\n v:=to_jsonb(NEW);\n SELECT * INTO r FROM @SCHEMA@.research_runs\n WHERE id=(v->>'run_id')::uuid AND owner_id=(v->>'owner_id')::uuid FOR UPDATE NOWAIT;\n IF NOT FOUND OR (r.purpose,r.engine,r.generation) IS DISTINCT FROM ('strategy_search','openevolve',1)\n OR (r.config->>'schema_version' IN ('mineagent.oe-structured-provider-run.v1','mineagent.oe-structured-provider-run.v2')) IS NOT TRUE\n THEN RAISE EXCEPTION 'Exact structured provider run required'; END IF;\n IF TG_TABLE_NAME IN ('oe_structured_submissions','oe_structured_specs') THEN\n  IF r.status IS DISTINCT FROM 'queued' OR r.active_attempt_id IS NOT NULL\n  THEN RAISE EXCEPTION 'Structured declaration requires original queued submission'; END IF;\n  RETURN NEW;\n END IF;\n IF r.status IS DISTINCT FROM 'running'\n THEN RAISE EXCEPTION 'Current structured provider run required'; END IF;\n SELECT * INTO a FROM @SCHEMA@.execution_attempts WHERE id=r.active_attempt_id AND run_id=r.id FOR UPDATE NOWAIT;\n IF NOT FOUND OR (a.id,a.generation,a.status) IS DISTINCT FROM\n ((v->>'attempt_id')::uuid,(v->>'generation')::integer,'running')\n OR a.generation IS DISTINCT FROM r.generation OR a.finished_at IS NOT NULL\n OR a.lease_expires_at IS NULL OR a.lease_expires_at<=clock_timestamp()\n THEN RAISE EXCEPTION 'Current structured provider attempt required'; END IF;\n RETURN NEW;\nEND $$;"

DOWNGRADE_SQL = "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_oes_append() RETURNS trigger\nLANGUAGE plpgsql SET search_path=pg_catalog AS $$\nDECLARE v jsonb; r record; a record;\nBEGIN\n IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Structured search evidence is immutable'; END IF;\n IF current_setting('transaction_isolation')<>'read committed'\n THEN RAISE EXCEPTION 'Structured search READ COMMITTED required'; END IF;\n v:=to_jsonb(NEW);\n SELECT * INTO r FROM @SCHEMA@.research_runs\n WHERE id=(v->>'run_id')::uuid AND owner_id=(v->>'owner_id')::uuid FOR UPDATE NOWAIT;\n IF NOT FOUND OR (r.purpose,r.engine,r.generation) IS DISTINCT FROM ('strategy_search','openevolve',1)\n OR (r.config->>'schema_version' IN ('mineagent.oe-structured-provider-run.v1','mineagent.oe-structured-provider-run.v2')) IS NOT TRUE\n THEN RAISE EXCEPTION 'Exact structured provider run required'; END IF;\n IF TG_TABLE_NAME IN ('oe_structured_submissions','oe_structured_specs') THEN\n  IF r.status IS DISTINCT FROM 'queued' OR r.active_attempt_id IS NOT NULL\n  THEN RAISE EXCEPTION 'Structured declaration requires original queued submission'; END IF;\n  RETURN NEW;\n END IF;\n IF r.status IS DISTINCT FROM 'running' OR r.finished_at IS NOT NULL\n THEN RAISE EXCEPTION 'Current structured provider run required'; END IF;\n SELECT * INTO a FROM @SCHEMA@.execution_attempts WHERE id=r.active_attempt_id AND run_id=r.id FOR UPDATE NOWAIT;\n IF NOT FOUND OR (a.id,a.generation,a.status) IS DISTINCT FROM\n ((v->>'attempt_id')::uuid,(v->>'generation')::integer,'running')\n OR a.generation IS DISTINCT FROM r.generation OR a.finished_at IS NOT NULL\n OR a.lease_expires_at IS NULL OR a.lease_expires_at<=clock_timestamp()\n THEN RAISE EXCEPTION 'Current structured provider attempt required'; END IF;\n RETURN NEW;\nEND $$;"
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
        "research_runs",
        "execution_attempts",
        "oe_structured_submissions",
        "oe_structured_specs",
        "oe_structured_epochs",
        "oe_structured_control_states",
        "oe_structured_sampling_intents",
        "oe_structured_worker_placements",
        "oe_structured_iteration_intents",
        "oe_structured_provider_transports",
        "oe_structured_provider_receipts",
        "oe_structured_provider_exchanges",
        "oe_structured_candidates",
        "oe_structured_executions",
        "oe_structured_engine_outcomes",
        "oe_structured_worker_outcomes",
        "oe_structured_terminal_applications",
        "oe_structured_checkpoint_intents",
        "oe_structured_checkpoint_uploads",
        "oe_structured_checkpoint_chunks",
        "oe_structured_checkpoint_completions",
        "oe_structured_checkpoint_publications",
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
