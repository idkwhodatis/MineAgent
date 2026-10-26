"""Revision018 deferred agreement of versioned shared rows and RD source graph."""

TABLES = (
    "research_runs",
    "research_operation_contexts",
    "research_model_operations",
    "research_budget_controls",
    "research_budget_reservations",
    "research_objects",
    "research_object_publications",
    "research_process_admissions",
    "factor_research_specs",
    "factor_research_attempt_bindings",
    "factor_generation_starts",
    "factor_generation_model_operations",
    "factor_controller_launches",
)

SQL = r"""
CREATE FUNCTION mineagent_rd_shared_consistent() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE owner_uuid uuid; r record; spec record; b record; cl record; pa record; ab record; expected jsonb;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM research_model_operations op JOIN research_runs rr ON rr.id=op.run_id
   JOIN research_operation_contexts cx ON cx.id=op.context_id
   WHERE op.owner_id=owner_uuid AND (
    (rr.engine='legacy' AND (op.protocol<>'mineagent.neutral-model.v1' OR cx.context->>'schema_version'<>'mineagent.neutral-fixture-context.v1'))
    OR (rr.engine='rdagent' AND (op.protocol NOT IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1') OR cx.context->>'schema_version'<>'mineagent.rdagent-context.v1'
       OR mineagent_rd_source_request(op.request,op.protocol) IS NOT TRUE
       OR op.request_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')))))
 THEN RAISE EXCEPTION 'Run/context/model protocol disagreement'; END IF;
 IF EXISTS(SELECT 1 FROM research_operation_contexts cx JOIN research_runs rr ON rr.id=cx.run_id
   WHERE cx.owner_id=owner_uuid AND ((rr.engine='legacy' AND cx.context->>'schema_version'<>'mineagent.neutral-fixture-context.v1')
    OR (rr.engine='rdagent' AND mineagent_rd_source_context(cx.context) IS NOT TRUE)))
 THEN RAISE EXCEPTION 'Run/context version disagreement'; END IF;
 IF EXISTS(SELECT 1 FROM factor_generation_model_operations m JOIN research_model_operations op ON op.id=m.operation_id
   WHERE m.owner_id=owner_uuid AND (m.protocol<>op.protocol OR m.context_id<>op.context_id))
 THEN RAISE EXCEPTION 'RD request membership differs from exact protocol'; END IF;
 IF EXISTS(SELECT 1 FROM research_budget_reservations br JOIN research_runs rr ON rr.id=br.run_id
   WHERE br.owner_id=owner_uuid AND br.purpose='engine_mutation' AND
    (rr.engine<>'rdagent' OR br.amounts<>'{"calls":0,"tokens":0,"cost_microunits":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}'::jsonb
     OR NOT EXISTS(SELECT 1 FROM factor_generation_starts st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation))))
 THEN RAISE EXCEPTION 'RD mutation slot requires exact start consumer'; END IF;
 IF EXISTS(SELECT 1 FROM research_object_publications p JOIN research_runs rr ON rr.id=p.run_id
   WHERE p.owner_id=owner_uuid AND p.object_kind='factor_response_assembly' AND rr.engine<>'rdagent')
 THEN RAISE EXCEPTION 'RD assembly publication cannot use legacy run'; END IF;
 FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid AND engine='rdagent' LOOP
  SELECT * INTO spec FROM factor_research_specs WHERE run_id=r.id;
  IF NOT FOUND OR spec.owner_id<>r.owner_id OR spec.config<>r.config
   OR spec.config_sha256<>r.request_sha256 OR spec.input_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(r.config->'input',true),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'RD immutable spec/run agreement'; END IF;
  SELECT * INTO b FROM research_budget_controls WHERE run_id=r.id;
  IF NOT FOUND OR b.owner_id<>r.owner_id OR b.limits<>r.config->'budget'
  THEN RAISE EXCEPTION 'RD immutable global budget differs'; END IF;
  IF EXISTS(SELECT 1 FROM research_operation_contexts cx WHERE cx.run_id=r.id AND
    (cx.context->>'spec_sha256'<>spec.config_sha256 OR cx.context->>'input_sha256'<>spec.input_sha256
     OR (cx.context->>'logical_step')::integer >= (r.config->>'max_iterations')::integer
     OR cx.context_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(cx.context,false),'UTF8')),'hex')))
  THEN RAISE EXCEPTION 'RD context differs from frozen specification'; END IF;
 END LOOP;
 FOR cl IN SELECT * FROM factor_controller_launches WHERE owner_id=owner_uuid LOOP
  SELECT * INTO ab FROM factor_research_attempt_bindings WHERE id=cl.attempt_binding_id;
  SELECT * INTO spec FROM factor_research_specs WHERE id=ab.spec_id;
  SELECT * INTO pa FROM research_process_admissions WHERE id=cl.admission_id;
  expected:=jsonb_build_object('schema_version','mineagent.rdagent-operational-bootstrap.v1',
   'owner_id',cl.owner_id,'run_id',cl.run_id,'attempt_id',cl.attempt_id,'generation',cl.generation,
   'spec_id',spec.id,'attempt_binding_id',ab.id,'config',spec.config,'config_sha256',spec.config_sha256,
   'input_sha256',spec.input_sha256,'continuation','fresh_only','input_layout','single_bootstrap_json_no_other_mounts');
  IF ab.resumed_checkpoint_id IS NOT NULL OR cl.bootstrap IS DISTINCT FROM expected
   OR cl.bootstrap_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(expected,true),'UTF8')),'hex')
   OR (pa.schema_version,pa.ipc_policy,pa.role,pa.profile,pa.logical_operation_id,pa.input_sha256,pa.image_id)
     IS DISTINCT FROM ('mineagent.process-admission.v2'::varchar,'host_ticket'::varchar,'rd_orchestrator'::varchar,'mineagent.rdagent-orchestrator.v1'::varchar,ab.id,cl.bootstrap_sha256,spec.config->'runtime'->>'orchestrator_image')
  THEN RAISE EXCEPTION 'RD controller persistence binding differs'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""


def render_sql():
    return SQL + "\n".join(
        f"CREATE CONSTRAINT TRIGGER rd_shared_semantics AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_rd_shared_consistent();"
        for table in TABLES
    )


RUN_LOCK_SQL = r"""
CREATE FUNCTION mineagent_rd_source_run_lock() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE run_uuid uuid; actual_engine text; actual_owner uuid; supplied_owner uuid;
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN
   run_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
 ELSE
   run_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END;
 END IF;
 -- No advisory lock is acquired late. The trusted transaction already owns this
 -- exact row; direct SQL either serializes on it or fails immediately.
 SELECT engine,owner_id INTO actual_engine,actual_owner FROM research_runs WHERE id=run_uuid;
 IF actual_engine='rdagent' THEN
   IF TG_TABLE_NAME<>'execution_attempts' THEN
     supplied_owner:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
     IF supplied_owner IS DISTINCT FROM actual_owner THEN RAISE EXCEPTION 'RD participant owner/run mismatch'; END IF;
   END IF;
   PERFORM 1 FROM research_runs WHERE id=run_uuid FOR UPDATE NOWAIT;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
"""


def run_lock_sql(domain_tables):
    tables = set(domain_tables) | {
        "research_runs",
        "execution_attempts",
        "research_budget_controls",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_operation_contexts",
        "research_context_aborts",
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
        "research_object_publications",
        "research_process_admissions",
        "research_process_receipts",
        "research_process_states",
        "engine_object_reservation_consumers",
    }
    return RUN_LOCK_SQL + "\n".join(
        f"CREATE TRIGGER a_rd_source_run_lock BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_rd_source_run_lock();"
        for table in sorted(tables)
    )
