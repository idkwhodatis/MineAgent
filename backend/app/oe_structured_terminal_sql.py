"""027 worker/application predicates; no terminal or migration activation."""

from packages.openevolve_structured.checkpoint import METADATA_FIELDS, PROGRAM_FIELDS

POPULATION = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_population_check(cid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE control record; c record; p record; o record; member record; state jsonb; program jsonb; metrics jsonb;
BEGIN
 SELECT * INTO control FROM @SCHEMA@.oe_structured_control_states WHERE id=cid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured population control missing'; END IF;
 state:=control.state_canonical::jsonb;
 IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(state) k) IS DISTINCT FROM ARRAY['metadata','programs']::text[]
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(state->'metadata') k) IS DISTINCT FROM @METADATA_KEYS@
 OR jsonb_typeof(state->'programs') IS DISTINCT FROM 'object'
 OR jsonb_typeof(state->'metadata') IS DISTINCT FROM 'object'
 OR (state->'metadata'->>'last_iteration') IS DISTINCT FROM control.frontier::text
 OR (SELECT count(*) FROM jsonb_object_keys(state->'programs')) NOT BETWEEN 1 AND 3
 THEN RAISE EXCEPTION 'Structured population shape'; END IF;
 FOR member IN SELECT key,value FROM jsonb_each(state->'programs') LOOP
  program:=member.value;
  SELECT * INTO c FROM @SCHEMA@.oe_structured_candidates WHERE epoch_id=control.epoch_id AND upstream_program_id::text=member.key;
  IF NOT FOUND THEN RAISE EXCEPTION 'Structured native population has no candidate'; END IF;
  SELECT * INTO p FROM @SCHEMA@.oe_structured_candidates WHERE id=c.parent_candidate_id;
  SELECT * INTO o FROM @SCHEMA@.oe_structured_engine_outcomes WHERE candidate_id=c.id;
  PERFORM @SCHEMA@.mineagent_oes_outcome_check(o.id);
  metrics:=(CASE WHEN (o.measurement_canonical::jsonb->>'eligible')::boolean
   THEN jsonb_build_object('combined_score',o.measurement_canonical::jsonb->'combined_score') ELSE '{}'::jsonb END);
  IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(program) k) IS DISTINCT FROM @PROGRAM_KEYS@
  OR (c.owner_id,c.run_id,c.attempt_id,c.generation) IS DISTINCT FROM
   (control.owner_id,control.run_id,control.attempt_id,control.generation)
  OR c.ordinal>control.frontier
  OR (program->>'id',program->>'code',program->>'language',program->>'parent_id',program->>'iteration_found') IS DISTINCT FROM
   (member.key,c.candidate_canonical,'json',p.upstream_program_id::text,c.ordinal::text)
  OR program->'metrics' IS DISTINCT FROM metrics
  OR jsonb_typeof(program->'artifacts_json') IS DISTINCT FROM 'string'
  OR (program->>'artifacts_json')::jsonb IS DISTINCT FROM jsonb_build_object('structured_feedback',o.measurement_canonical)
  OR program->'prompts' IS DISTINCT FROM 'null'::jsonb
  OR program->'artifact_dir' IS DISTINCT FROM 'null'::jsonb
  THEN RAISE EXCEPTION 'Structured population values differ from native result'; END IF;
 END LOOP;
END $$;
"""

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_worker_check(wid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE w record; i record; c record; p record; o record; j jsonb; child jsonb; metrics jsonb;
BEGIN
 SELECT * INTO w FROM @SCHEMA@.oe_structured_worker_outcomes WHERE id=wid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured worker outcome missing'; END IF;
 SELECT * INTO i FROM @SCHEMA@.oe_structured_iteration_intents WHERE id=w.iteration_intent_id;
 j:=w.evidence_canonical::jsonb;
 IF (w.owner_id,w.run_id,w.attempt_id,w.generation,w.epoch_id) IS DISTINCT FROM
 (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id)
 OR @SCHEMA@.mineagent_structured_raw_commitment(w.evidence_canonical,j,w.evidence_sha256,131072) IS NOT TRUE
 OR j->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-worker-outcome.v1'
 OR (j->>'event_sha256',j->'projection'->>'epoch_id',j->'projection'->>'intent_id',j->'projection'->>'native_iteration',j->'projection'->>'status',j->'observation'->>'iteration',j->'observation'->>'outcome') IS DISTINCT FROM
 (w.event_sha256,w.epoch_id::text,i.id::text,i.ordinal::text,w.status,i.ordinal::text,w.status)
 OR j->'projection'->>'objective_ack_id' IS DISTINCT FROM w.engine_outcome_id::text
 THEN RAISE EXCEPTION 'Structured actual worker projection differs'; END IF;
 IF w.status='worker_error' THEN
  IF (w.candidate_id,w.execution_id,w.engine_outcome_id) IS DISTINCT FROM (NULL::uuid,NULL::uuid,NULL::uuid)
  OR j->'observation'->'child' IS DISTINCT FROM 'null'::jsonb
  OR j->'observation'->'parent_id' IS DISTINCT FROM 'null'::jsonb
  OR j->'observation'->'target_island' IS DISTINCT FROM 'null'::jsonb
  THEN RAISE EXCEPTION 'Structured error fabricated evaluation'; END IF;
 ELSE
  IF w.status IS DISTINCT FROM 'evaluated' THEN RAISE EXCEPTION 'Structured worker status'; END IF;
  SELECT * INTO c FROM @SCHEMA@.oe_structured_candidates WHERE id=w.candidate_id;
  SELECT * INTO p FROM @SCHEMA@.oe_structured_candidates WHERE id=i.parent_candidate_id;
  SELECT * INTO o FROM @SCHEMA@.oe_structured_engine_outcomes WHERE id=w.engine_outcome_id;
  PERFORM @SCHEMA@.mineagent_oes_outcome_check(o.id);
  child:=j->'observation'->'child';
  metrics:=(CASE WHEN (o.measurement_canonical::jsonb->>'eligible')::boolean
    THEN jsonb_build_object('combined_score',o.measurement_canonical::jsonb->'combined_score') ELSE '{}'::jsonb END);
  IF (o.owner_id,o.run_id,o.attempt_id,o.generation,o.candidate_id,o.execution_id) IS DISTINCT FROM
  (w.owner_id,w.run_id,w.attempt_id,w.generation,w.candidate_id,w.execution_id)
  OR (c.epoch_id,c.ordinal,c.parent_candidate_id) IS DISTINCT FROM (w.epoch_id,i.ordinal,p.id)
  OR (child->>'id',child->>'code',child->>'parent_id',child->>'iteration_found',child->>'language',j->'observation'->>'parent_id',j->'observation'->>'target_island') IS DISTINCT FROM
  (c.upstream_program_id::text,c.candidate_canonical,p.upstream_program_id::text,i.ordinal::text,'json',p.upstream_program_id::text,'0')
  OR child->'metrics' IS DISTINCT FROM metrics
  OR child->'embedding' IS DISTINCT FROM 'null'::jsonb
  OR child->'artifacts_json' IS DISTINCT FROM 'null'::jsonb
  OR child->'artifact_dir' IS DISTINCT FROM 'null'::jsonb
  OR child->'prompts' IS DISTINCT FROM 'null'::jsonb
  THEN RAISE EXCEPTION 'Structured evaluated child differs from actual outcome'; END IF;
 END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_application_check(tid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE t record; c record; previous record; w record; i record; b record; child record; j jsonb; before_state jsonb; after_state jsonb;
BEGIN
 SELECT * INTO t FROM @SCHEMA@.oe_structured_terminal_applications WHERE id=tid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured terminal application missing'; END IF;
 SELECT * INTO c FROM @SCHEMA@.oe_structured_control_states WHERE id=t.control_state_id;
 PERFORM @SCHEMA@.mineagent_oes_control_check(c.id);
 PERFORM @SCHEMA@.mineagent_oes_population_check(c.id);
 j:=t.event_canonical::jsonb; after_state:=c.state_canonical::jsonb;
 IF (t.owner_id,t.run_id,t.attempt_id,t.generation,t.epoch_id,t.ordinal) IS DISTINCT FROM
 (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.frontier)
 OR c.sequence IS DISTINCT FROM 2*t.ordinal+1
 OR @SCHEMA@.mineagent_structured_raw_commitment(t.event_canonical,j,t.event_sha256,1048576) IS NOT TRUE
 OR (j->>'iteration',j->>'outcome',j->>'state_sha256',j->'native_state') IS DISTINCT FROM
 (t.ordinal::text,t.outcome,c.state_sha256,after_state)
 THEN RAISE EXCEPTION 'Structured terminal application/control differs'; END IF;
 IF t.ordinal=0 THEN
  IF t.outcome IS DISTINCT FROM 'seed' OR t.worker_outcome_id IS NOT NULL
  OR (SELECT count(*) FROM jsonb_object_keys(after_state->'programs'))<>1
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_candidates seed WHERE seed.epoch_id=t.epoch_id AND seed.ordinal=0 AND after_state->'programs' ? seed.upstream_program_id::text)
  THEN RAISE EXCEPTION 'Structured seed application differs'; END IF;
 ELSE
  SELECT * INTO w FROM @SCHEMA@.oe_structured_worker_outcomes WHERE id=t.worker_outcome_id;
  PERFORM @SCHEMA@.mineagent_oes_worker_check(w.id);
  SELECT * INTO i FROM @SCHEMA@.oe_structured_iteration_intents WHERE id=w.iteration_intent_id;
  SELECT * INTO previous FROM @SCHEMA@.oe_structured_control_states WHERE id=c.previous_id;
  SELECT r.* INTO b FROM @SCHEMA@.research_budget_reservations r JOIN @SCHEMA@.oe_structured_sampling_intents si ON si.mutation_reservation_id=r.id WHERE si.id=i.id;
  before_state:=previous.state_canonical::jsonb;
  SELECT * INTO child FROM @SCHEMA@.oe_structured_candidates WHERE id=w.candidate_id;
  IF (w.owner_id,w.run_id,w.attempt_id,w.generation,w.epoch_id,i.ordinal,i.sampling_state_id) IS DISTINCT FROM
  (t.owner_id,t.run_id,t.attempt_id,t.generation,t.epoch_id,t.ordinal,previous.id)
  OR (t.outcome='error') IS DISTINCT FROM (w.status='worker_error')
  OR t.outcome NOT IN ('accepted','rejected','error')
  OR (t.outcome='accepted' AND (child.id IS NULL
   OR (after_state->'programs')->(child.upstream_program_id::text) IS NULL
   OR ((after_state->'programs')-(child.upstream_program_id::text)) IS DISTINCT FROM before_state->'programs'))
  OR b.state IS DISTINCT FROM 'settled'
  OR b.amounts IS DISTINCT FROM jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'mutation_slots',1,'evaluator_executions',0,'wall_milliseconds',0,'storage_bytes',0,'process_slots',0)
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements paid WHERE paid.reservation_id=b.id
   AND (paid.owner_id,paid.run_id,paid.attempt_id,paid.generation)=(t.owner_id,t.run_id,t.attempt_id,t.generation)
   AND paid.charge=b.amounts AND paid.outcome='conservative_unknown')
  OR (t.outcome IN ('rejected','error') AND (after_state-'metadata' IS DISTINCT FROM before_state-'metadata'
   OR after_state->'metadata'-'last_iteration' IS DISTINCT FROM before_state->'metadata'-'last_iteration'))
  THEN RAISE EXCEPTION 'Structured applied worker/charge/unchanged rejection differs'; END IF;
 END IF;
END $$;
"""


def render_upgrade_sql():
    def keys(values):
        return (
            "ARRAY[" + ",".join("'" + key + "'" for key in sorted(values)) + "]::text[]"
        )

    return (
        (POPULATION + SQL)
        .replace("@PROGRAM_KEYS@", keys(PROGRAM_FIELDS))
        .replace("@METADATA_KEYS@", keys(METADATA_FIELDS))
    )


def render_downgrade_sql():
    return """DROP FUNCTION @SCHEMA@.mineagent_oes_application_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_worker_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_population_check(uuid);"""
