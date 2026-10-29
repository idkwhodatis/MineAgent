"""027 append fencing and bounded deferred reverse checks, not activation.

The literal assembler installs these only after all domain predicates. Changes
on shared dependency rows must revalidate consumers, including the OLD side of
an update. No physical validation or caller authorization is inferred by SQL.
"""

from backend.app.oe_structured_checkpoint_sql import TABLES as CHECKPOINT_TABLES
from backend.app.oe_structured_provider_sql import TABLES as PROVIDER_TABLES

TABLES = (
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
SHARED = tuple(
    sorted(
        set(
            PROVIDER_TABLES
            + CHECKPOINT_TABLES
            + (
                "research_runs",
                "execution_attempts",
                "research_budget_controls",
                "research_process_admissions",
                "research_process_states",
                "research_process_receipts",
                "structured_strategy_versions",
                "dataset_versions",
                "factor_execution_snapshot_publications",
                "factor_execution_snapshot_outcomes",
                "data_usage_exposures",
            )
        )
        - set(TABLES)
    )
)

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v jsonb; r record; a record;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Structured search evidence is immutable'; END IF;
 IF current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'Structured search READ COMMITTED required'; END IF;
 v:=to_jsonb(NEW);
 SELECT * INTO r FROM @SCHEMA@.research_runs
 WHERE id=(v->>'run_id')::uuid AND owner_id=(v->>'owner_id')::uuid FOR UPDATE NOWAIT;
 IF NOT FOUND OR (r.purpose,r.engine,r.generation) IS DISTINCT FROM ('strategy_search','openevolve',1)
 OR r.config->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v1'
 THEN RAISE EXCEPTION 'Exact structured provider run required'; END IF;
 IF TG_TABLE_NAME IN ('oe_structured_submissions','oe_structured_specs') THEN
  IF r.status IS DISTINCT FROM 'queued' OR r.active_attempt_id IS NOT NULL
  THEN RAISE EXCEPTION 'Structured declaration requires original queued submission'; END IF;
  RETURN NEW;
 END IF;
 IF r.status IS DISTINCT FROM 'running' OR r.finished_at IS NOT NULL
 THEN RAISE EXCEPTION 'Current structured provider run required'; END IF;
 SELECT * INTO a FROM @SCHEMA@.execution_attempts WHERE id=r.active_attempt_id AND run_id=r.id FOR UPDATE NOWAIT;
 IF NOT FOUND OR (a.id,a.generation,a.status) IS DISTINCT FROM
 ((v->>'attempt_id')::uuid,(v->>'generation')::integer,'running')
 OR a.generation IS DISTINCT FROM r.generation OR a.finished_at IS NOT NULL
 OR a.lease_expires_at IS NULL OR a.lease_expires_at<=clock_timestamp()
 THEN RAISE EXCEPTION 'Current structured provider attempt required'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE versions jsonb[]; v jsonb; ids uuid[]; target uuid; rid uuid;
BEGIN
 versions:=(CASE WHEN TG_OP='INSERT' THEN ARRAY[to_jsonb(NEW)]
  WHEN TG_OP='DELETE' THEN ARRAY[to_jsonb(OLD)] ELSE ARRAY[to_jsonb(OLD),to_jsonb(NEW)] END);
 FOREACH v IN ARRAY versions LOOP
  rid:=NULL;
  IF TG_TABLE_NAME='research_runs' THEN rid:=(v->>'id')::uuid;
  ELSIF v ? 'run_id' THEN rid:=(v->>'run_id')::uuid;
  END IF;
  IF rid IS NOT NULL THEN ids:=ARRAY[rid];
  ELSE
   -- Owner-bound source/object/version changes can invalidate several retained
   -- declarations. Use a bounded conservative reverse set, never just NEW FKs.
   SELECT array_agg(run_id) INTO ids FROM (
    SELECT run_id FROM @SCHEMA@.oe_structured_specs WHERE owner_id=(v->>'owner_id')::uuid
    ORDER BY run_id LIMIT 10001
   ) q;
   IF cardinality(ids)>10000 THEN RAISE EXCEPTION 'Structured reverse graph bound'; END IF;
  END IF;
  FOREACH target IN ARRAY coalesce(ids,ARRAY[]::uuid[]) LOOP
   IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_specs WHERE run_id=target)
   AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE id=target
    AND config->>'schema_version'='mineagent.oe-structured-provider-run.v1') THEN CONTINUE; END IF;
   IF current_setting('transaction_isolation')<>'read committed'
   THEN RAISE EXCEPTION 'Structured search READ COMMITTED required'; END IF;
   -- Serializes against submission/cancellation/fence changes, not only the
   -- graph row being inserted. NOWAIT preserves the maintenance lock policy.
   PERFORM 1 FROM @SCHEMA@.research_runs WHERE id=target FOR UPDATE NOWAIT;
   PERFORM @SCHEMA@.mineagent_oes_graph_check(target);
  END LOOP;
 END LOOP;
 RETURN NULL;
END $$;
"""


def render_upgrade_sql():
    statements = [SQL]
    for table in TABLES:
        statements.append(
            f"CREATE TRIGGER oes027_append BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} "
            "FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oes_append();"
        )
    for table in (*TABLES, *SHARED):
        statements.append(
            f"CREATE CONSTRAINT TRIGGER oes027_consistent AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oes_consistent();"
        )
    return "\n".join(statements)


def render_downgrade_sql():
    statements = [
        f"DROP TRIGGER oes027_consistent ON @SCHEMA@.{table};"
        for table in reversed((*TABLES, *SHARED))
    ]
    statements += [
        f"DROP TRIGGER oes027_append ON @SCHEMA@.{table};" for table in reversed(TABLES)
    ]
    statements += [
        "DROP FUNCTION @SCHEMA@.mineagent_oes_consistent();",
        "DROP FUNCTION @SCHEMA@.mineagent_oes_append();",
    ]
    return "\n".join(statements)
