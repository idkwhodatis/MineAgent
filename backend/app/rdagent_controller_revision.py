"""Explicit020 deltas over the frozen018 SQL; never regenerate historical DDL."""

import ast
import hashlib
from pathlib import Path

FROZEN_018_SQL_SHA256 = (
    "57047b1e2de302c2cc19e4f64cd974f89555ebc201fc9e90fc3cc727bf966715"
)


def frozen_source_sql():
    path = Path(__file__).parents[1] / "alembic/versions/018_rd_source_persistence.py"
    module = ast.parse(path.read_text())
    value = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if hashlib.sha256(value.encode()).hexdigest() != FROZEN_018_SQL_SHA256:
        raise ValueError(
            "Frozen018 SQL drift; do not infer a successor from current ORM"
        )
    return value


def _function(sql, name):
    markers = (
        "CREATE FUNCTION " + name + "(",
        "CREATE FUNCTION @SCHEMA@." + name + "(",
    )
    matches = [sql.index(marker) for marker in markers if marker in sql]
    if len(matches) != 1:
        raise ValueError("Exact historical function required")
    start = matches[0]
    stop = sql.index("END $$;", start) + len("END $$;")
    return sql[start:stop].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def profile_delta_sql():
    sql = frozen_source_sql()
    shared = _function(sql, "mineagent_rd_shared_consistent")
    old = "pa.schema_version,pa.ipc_policy,pa.role,pa.profile,pa.logical_operation_id,pa.input_sha256,pa.image_id"
    if shared.count(old) != 1:
        raise ValueError("Historical tuple drift")
    shared = shared.replace(old, old.replace("pa.profile,", ""))
    shared = shared.replace(
        "'rd_orchestrator'::varchar,'mineagent.rdagent-orchestrator.v1'::varchar,",
        "'rd_orchestrator'::varchar,",
    )
    shared = shared.replace(
        "  THEN RAISE EXCEPTION 'RD controller persistence binding differs'",
        "   OR pa.profile NOT IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')\n  THEN RAISE EXCEPTION 'RD controller persistence binding differs'",
    )
    source = _function(sql, "mineagent_rd_source_violation")
    old = "pa.profile='mineagent.rdagent-orchestrator.v1'"
    if source.count(old) != 1:
        raise ValueError("Historical source profile drift")
    source = source.replace(
        old,
        "pa.profile IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')",
    )
    return shared + "\n" + source


BOUNDARY_SQL = r"""
ALTER TABLE factor_generation_boundaries ADD CONSTRAINT uq_rd_boundary_execution_actor
 UNIQUE(owner_id,run_id,attempt_id,generation,id,controller_launch_id,start_id);
CREATE FUNCTION mineagent_rd_operational_actor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE admission_uuid uuid;
BEGIN
 SELECT cl.admission_id INTO admission_uuid FROM factor_controller_launches cl
 JOIN research_process_admissions p ON p.id=cl.admission_id
 JOIN research_process_states s ON s.admission_id=p.id
 WHERE cl.id=NEW.controller_launch_id
 AND (cl.owner_id,cl.run_id,cl.attempt_id,cl.generation)=(NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation)
 AND p.profile='mineagent.rdagent-orchestrator.v2' AND s.status='running'
 AND EXISTS(SELECT 1 FROM research_process_receipts r WHERE r.admission_id=p.id AND r.kind='controller_child');
 IF admission_uuid IS NULL THEN RAISE EXCEPTION 'Current protected controller required'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION mineagent_rd_operational_boundary_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_uuid uuid; o record; st record; src record; cl record; expected jsonb; c record; co record; members jsonb; outcomes jsonb; count_members integer;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM factor_generation_sources source
 JOIN factor_source_read_witnesses w ON w.source_id=source.id
 JOIN factor_controller_launches launch ON launch.id=w.controller_launch_id
 JOIN research_process_admissions p ON p.id=launch.admission_id
 WHERE source.owner_id=owner_uuid AND p.profile='mineagent.rdagent-orchestrator.v2'
 AND NOT EXISTS(SELECT 1 FROM research_process_receipts r WHERE r.admission_id=p.id AND r.kind='controller_child'))
 THEN RAISE EXCEPTION 'Operational source requires retained child identity'; END IF;
 FOR o IN SELECT * FROM factor_generation_observations WHERE owner_id=owner_uuid LOOP
  SELECT * INTO st FROM factor_generation_starts WHERE id=o.start_id;
  SELECT * INTO src FROM factor_generation_sources WHERE start_id=st.id;
  SELECT * INTO cl FROM factor_controller_launches WHERE id=o.controller_launch_id;
  IF src.id IS NULL OR st.logical_step<>0 OR o.logical_step<>0
   OR cl.bootstrap->'config'->>'max_iterations'<>'1'
   OR (st.owner_id,st.run_id,st.attempt_id,st.generation) IS DISTINCT FROM (o.owner_id,o.run_id,o.attempt_id,o.generation)
  THEN RAISE EXCEPTION 'Initial actual generation boundary required'; END IF;
  expected:=jsonb_build_object('schema_version','mineagent.rdagent-adoption-observation.v1','logical_step',0,
   'evaluated_source_id',src.id,'previous_adopted_source_id',NULL,'adopted_source_id',src.id,
   'continuation_source_id',src.id,'adopted_new',true);
  IF o.decision IS DISTINCT FROM expected OR o.decision_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(expected,true),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'Exact first generation adoption required'; END IF;
  IF NOT EXISTS(SELECT 1 FROM factor_upstream_calls WHERE start_id=st.id AND phase='knowledge_update' AND kind='embedding')
  THEN RAISE EXCEPTION 'Actual upstream knowledge boundary required'; END IF;
  FOR c IN SELECT * FROM factor_upstream_calls WHERE start_id=st.id LOOP
   SELECT * INTO co FROM factor_upstream_call_outcomes WHERE call_id=c.id;
   SELECT count(*),jsonb_agg(m.id::text ORDER BY m.request_ordinal),jsonb_agg(mo.id::text ORDER BY m.request_ordinal)
    INTO count_members,members,outcomes FROM factor_generation_model_operations m
    JOIN research_model_operations op ON op.id=m.operation_id AND op.state='completed'
    JOIN research_model_transports mt ON mt.operation_id=op.id AND mt.state='completed'
    JOIN research_model_outcomes mo ON mo.transport_id=mt.id AND mo.outcome='completed'
    WHERE m.call_id=c.id AND m.protocol=CASE c.kind WHEN 'chat' THEN 'mineagent.rdagent-chat.v1' ELSE 'mineagent.rdagent-embedding.v1' END;
   IF co.id IS NULL OR co.status<>'completed' OR co.controller_launch_id<>cl.id OR count_members<1
    OR count_members<>(SELECT count(*) FROM factor_generation_model_operations WHERE call_id=c.id)
    OR (SELECT min(request_ordinal) FROM factor_generation_model_operations WHERE call_id=c.id)<>0
    OR (SELECT max(request_ordinal) FROM factor_generation_model_operations WHERE call_id=c.id)<>count_members-1
    OR co.member_count<>count_members OR co.member_ids IS DISTINCT FROM members OR co.response_outcome_ids IS DISTINCT FROM outcomes
   THEN RAISE EXCEPTION 'All actual model calls must close before adoption'; END IF;
  END LOOP;
 END LOOP;
 IF EXISTS(SELECT 1 FROM factor_generation_boundaries b JOIN factor_generation_observations boundary_observation ON boundary_observation.id=b.observation_id
 WHERE b.owner_id=owner_uuid AND (b.boundary<>'returned' OR b.next_logical_step IS NOT NULL
  OR (b.start_id,b.controller_launch_id,b.logical_step) IS DISTINCT FROM (boundary_observation.start_id,boundary_observation.controller_launch_id,boundary_observation.logical_step)))
 THEN RAISE EXCEPTION 'Exact returned first generation boundary required'; END IF;
 RETURN NULL;
END $$;
"""


def boundary_delta_sql():
    sql = BOUNDARY_SQL
    for table in ("factor_generation_observations", "factor_generation_boundaries"):
        sql += f"\nDROP TRIGGER rd_source_phase_closed ON {table};\nCREATE TRIGGER rd_operational_actor BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_rd_operational_actor();"
    for table in (
        "factor_generation_sources",
        "factor_source_read_witnesses",
        "factor_generation_observations",
        "factor_generation_boundaries",
        "factor_upstream_calls",
        "factor_upstream_call_outcomes",
        "factor_generation_model_operations",
        "research_process_receipts",
    ):
        sql += f"\nCREATE CONSTRAINT TRIGGER rd_operational_boundary AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_rd_operational_boundary_consistent();"
    return sql
