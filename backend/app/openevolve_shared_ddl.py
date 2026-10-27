"""Explicit024 shared-table predicates, preserving installed020/022 contracts.

No migration activation or process acceptance is performed here. Install only
with the024 domain tables, OE identity functions and separate exact protected
process semantic guards. Raw float-bearing bootstrap/result commitments remain
with their own versioned validators; these checks do not reserialize them.
"""

import re

PRIOR_RD_SHARED = r"""
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_shared_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
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
    (rr.engine<>'rdagent' OR br.amounts<>'{"calls":0,"tokens":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}'::jsonb
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
   OR (pa.schema_version,pa.ipc_policy,pa.role,pa.logical_operation_id,pa.input_sha256,pa.image_id)
     IS DISTINCT FROM ('mineagent.process-admission.v2'::varchar,'host_ticket'::varchar,'rd_orchestrator'::varchar,ab.id,cl.bootstrap_sha256,spec.config->'runtime'->>'orchestrator_image')
   OR pa.profile NOT IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')
  THEN RAISE EXCEPTION 'RD controller persistence binding differs'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""

PRIOR_RD_CONTROLLER = r"""
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_controller_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE owner_uuid uuid; a record; rr record; br record; bs record; ps record;
 sp record; ab record; cl record; p record; intent jsonb; ident jsonb; child jsonb; stopped jsonb;
 decl jsonb; launch jsonb; expected_bootstrap jsonb; report jsonb; n integer;
 intent_hash text; identity_hash text; child_hash text; launch_hash text;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM research_process_receipts child_receipt JOIN research_process_admissions child_admission ON child_admission.id=child_receipt.admission_id
 WHERE child_receipt.owner_id=owner_uuid AND child_receipt.kind='controller_child' AND
  (child_admission.profile<>'mineagent.rdagent-orchestrator.v2' OR child_admission.role<>'rd_orchestrator'))
 THEN RAISE EXCEPTION 'RD child cannot extend historical process grammar'; END IF;
 FOR a IN SELECT * FROM research_process_admissions WHERE owner_id=owner_uuid AND profile='mineagent.rdagent-orchestrator.v2' LOOP
  IF (a.schema_version,a.role,a.ipc_policy) IS DISTINCT FROM
    ('mineagent.process-admission.v2'::varchar,'rd_orchestrator'::varchar,'host_ticket'::varchar)
    OR a.ticket_sha256 IS NULL OR a.ticket_sha256 !~ '^[a-f0-9]{64}$'
  THEN RAISE EXCEPTION 'Exact RD controller declaration required'; END IF;
  SELECT * INTO rr FROM research_runs WHERE id=a.run_id AND owner_id=a.owner_id;
  IF NOT FOUND OR rr.engine<>'rdagent' OR rr.purpose<>'factor_research'
  THEN RAISE EXCEPTION 'RD controller requires genuine RD run'; END IF;
  SELECT * INTO sp FROM factor_research_specs WHERE run_id=a.run_id AND owner_id=a.owner_id;
  IF NOT FOUND OR sp.config<>rr.config OR sp.config_sha256<>rr.request_sha256
  THEN RAISE EXCEPTION 'Controller immutable spec missing'; END IF;
  SELECT * INTO ab FROM factor_research_attempt_bindings WHERE id=a.logical_operation_id
   AND (owner_id,run_id,attempt_id,generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation);
  IF NOT FOUND OR ab.spec_id<>sp.id OR ab.resumed_checkpoint_id IS NOT NULL
  THEN RAISE EXCEPTION 'Controller attempt binding differs'; END IF;
  SELECT * INTO cl FROM factor_controller_launches WHERE admission_id=a.id
   AND (owner_id,run_id,attempt_id,generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation);
  IF NOT FOUND OR cl.attempt_binding_id<>ab.id THEN RAISE EXCEPTION 'Controller launch domain binding missing'; END IF;
  expected_bootstrap:=jsonb_build_object('schema_version','mineagent.rdagent-operational-bootstrap.v1',
   'owner_id',a.owner_id,'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,
   'spec_id',sp.id,'attempt_binding_id',ab.id,'config',sp.config,'config_sha256',sp.config_sha256,
   'input_sha256',sp.input_sha256,'continuation','fresh_only','input_layout','single_bootstrap_json_no_other_mounts');
  IF cl.bootstrap<>expected_bootstrap OR cl.bootstrap_sha256<>a.input_sha256
   OR a.input_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(expected_bootstrap,true),'UTF8')),'hex')
   OR a.image_id<>sp.config->'runtime'->>'orchestrator_image'
  THEN RAISE EXCEPTION 'Controller bootstrap/image commitment differs'; END IF;
  SELECT * INTO br FROM research_budget_reservations WHERE id=a.reservation_id;
  IF NOT FOUND OR (br.owner_id,br.run_id,br.attempt_id,br.generation) IS DISTINCT FROM
    (a.owner_id,a.run_id,a.attempt_id,a.generation) OR br.purpose<>'process_unit'
    OR (br.amounts->>'process_slots')::numeric<>1 OR (br.amounts->>'wall_milliseconds')::numeric<60000
    OR (br.amounts->>'storage_bytes')::numeric<67174400
    OR br.amounts->>'calls'<>'0' OR br.amounts->>'tokens'<>'0'
    OR br.amounts->>'mutation_slots'<>'0' OR br.amounts->>'evaluator_executions'<>'0'
  THEN RAISE EXCEPTION 'Controller physical reservation floor/scope differs'; END IF;
  SELECT * INTO ps FROM research_process_states WHERE admission_id=a.id;
  IF NOT FOUND OR (ps.owner_id,ps.run_id,ps.attempt_id,ps.generation) IS DISTINCT FROM
    (a.owner_id,a.run_id,a.attempt_id,a.generation) OR ps.status NOT IN ('launch_intended','running','uncertain','stopped')
  THEN RAISE EXCEPTION 'Controller projection differs'; END IF;
  intent:=NULL;ident:=NULL;child:=NULL;stopped:=NULL;
  FOR p IN SELECT * FROM research_process_receipts WHERE admission_id=a.id LOOP
   IF (p.owner_id,p.run_id,p.attempt_id,p.generation) IS DISTINCT FROM
      (a.owner_id,a.run_id,a.attempt_id,a.generation)
    OR @S@.mineagent_rd_controller_payload(p.kind,p.payload) IS NOT TRUE
    OR p.payload_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(p.payload,true),'UTF8')),'hex')
   THEN RAISE EXCEPTION 'Exact controller receipt grammar/hash required'; END IF;
   CASE p.kind WHEN 'launch_intent' THEN intent:=p.payload;
    WHEN 'launch_identity' THEN ident:=p.payload;
    WHEN 'controller_child' THEN child:=p.payload;
    WHEN 'stop' THEN stopped:=p.payload;
    ELSE RAISE EXCEPTION 'Unexpected controller receipt'; END CASE;
  END LOOP;
  IF intent IS NULL THEN RAISE EXCEPTION 'Controller launch intent required'; END IF;
  decl:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
   'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
   'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
   'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
   'ipc',jsonb_build_object('ipc_policy','host_ticket','ticket_sha256',a.ticket_sha256));
  IF intent->'admission'<>decl OR intent->>'bootstrap_sha256'<>a.input_sha256
   OR (intent->>'bootstrap_size_bytes')::integer<>octet_length(convert_to(@S@.mineagent_rd_canonical_json(expected_bootstrap,true),'UTF8'))
   OR (intent->>'not_after')::timestamptz <= (intent->>'issued_at')::timestamptz
   OR (intent->>'not_after')::timestamptz-(intent->>'issued_at')::timestamptz>interval '60 seconds'
  THEN RAISE EXCEPTION 'Controller immutable intent differs'; END IF;
  IF ps.status IN ('running','stopped') AND ident IS NULL THEN RAISE EXCEPTION 'Controller leader required'; END IF;
  IF (stopped IS NOT NULL) IS DISTINCT FROM (ps.status='stopped') THEN RAISE EXCEPTION 'Controller stopped projection differs'; END IF;
  IF ident IS NULL AND (child IS NOT NULL OR stopped IS NOT NULL) THEN RAISE EXCEPTION 'Controller leader predecessor missing'; END IF;
  IF ident IS NOT NULL THEN
   launch:=jsonb_build_object('schema_version','mineagent.rd-controller-launch.v1','intent',intent,
    'runtime_name','mineagent-rd-controller-'||a.id::text,'root_device',ident->'root_device','root_inode',ident->'root_inode');
   launch_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(launch,true),'UTF8')),'hex');
   identity_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(ident,true),'UTF8')),'hex');
   IF ident->>'launch_sha256'<>launch_hash OR ident->>'image_id'<>a.image_id
    OR ident->>'admission_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(decl,true),'UTF8')),'hex')
    OR @S@.mineagent_rd_controller_kernel(ident->'leader',0) IS NOT TRUE
   THEN RAISE EXCEPTION 'Protected leader physical identity differs'; END IF;
  END IF;
  child_hash:=NULL;
  IF child IS NOT NULL THEN
   n:=jsonb_array_length(child->'namespace_pids');
   IF child->>'identity_sha256'<>identity_hash OR child->>'admission_sha256'<>ident->>'admission_sha256'
    OR @S@.mineagent_rd_controller_kernel(child->'controller',65533) IS NOT TRUE
    OR child->'controller'->'ppid'<>ident->'leader'->'host_pid'
    OR child->'controller'->'host_pid'=ident->'leader'->'host_pid'
    OR child->'controller'->'pid_namespace'<>ident->'leader'->'pid_namespace'
    OR child->'controller'->'cgroup'<>ident->'leader'->'cgroup'
    OR child->'peer_pid'<>child->'controller'->'host_pid'
    OR child->'namespace_pids'->0<>child->'controller'->'host_pid'
    OR child->'namespace_pids'->(n-1)<>child->'namespace_pid'
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(child->'namespace_pids') x WHERE (x.value::text)::numeric<=0)
   THEN RAISE EXCEPTION 'Exact trusted child/peer/NSpid identity differs'; END IF;
   report:=jsonb_build_object('schema','mineagent.rdagent-controller-report.v1','event','started',
    'child_namespace_pid',child->'namespace_pid','child_start_ticks',child->'controller'->'start_ticks');
   IF child->>'started_report_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(report,true)||E'\n','UTF8')),'hex')
   THEN RAISE EXCEPTION 'Trusted startup frame commitment differs'; END IF;
   child_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(child,true),'UTF8')),'hex');
  END IF;
  IF stopped IS NOT NULL AND (stopped->>'launch_sha256'<>launch_hash
    OR stopped->>'identity_sha256'<>identity_hash OR stopped->>'container_id'<>ident->>'container_id'
    OR (stopped->>'child_sha256') IS DISTINCT FROM child_hash
    OR (stopped->'child_kernel_identity_absent') IS DISTINCT FROM CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END)
  THEN RAISE EXCEPTION 'Controller positive stop metadata differs'; END IF;
  SELECT * INTO bs FROM research_budget_settlements WHERE reservation_id=br.id;
  IF br.state='settled' THEN
   IF NOT FOUND OR stopped IS NULL OR bs.outcome<>'conservative_unknown'
    OR bs.charge IS DISTINCT FROM br.amounts
    OR (bs.owner_id,bs.run_id,bs.attempt_id,bs.generation) IS DISTINCT FROM
      (a.owner_id,a.run_id,a.attempt_id,a.generation)
   THEN RAISE EXCEPTION 'Controller settlement requires stopped full conservative charge'; END IF;
  ELSIF FOUND OR br.state NOT IN ('reserved','uncertain') THEN RAISE EXCEPTION 'Controller unresolved budget must remain held'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""


# These literal historical bodies are deliberately retained, not regenerated
# from an older018 precursor. Only finite exact replacements below are allowed.
def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Frozen shared SQL anchor differs")
    return text.replace(old, new)


def _safe_expressions(sql):
    # PL/pgSQL expression contexts are explicit; this is not statement CASE.
    sql = sql.replace(
        "owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;",
        "owner_uuid:=(CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END);",
    )
    return sql.replace(
        "IS DISTINCT FROM CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END)",
        "IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END))",
    )


def _complete_mutation_budget(sql):
    # Exact b5ad085 correction only: historical frozen020 inherited the missing
    # zero cost dimension from018. No subset/contains comparison is permitted.
    return sql.replace(
        '{"calls":0,"tokens":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}',
        '{"calls":0,"tokens":0,"cost_microunits":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}',
    )


def shared_consistency_sql():
    value = _complete_mutation_budget(_safe_expressions(PRIOR_RD_SHARED))
    value = _once(
        value,
        "OR op.request_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')))))",
        """OR op.request_sha256<>encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')))
    OR (rr.engine='openevolve' AND (op.protocol<>'mineagent.openevolve-chat.v1'
       OR mineagent_oe_request(op.request,op.protocol) IS NOT TRUE
       OR mineagent_oe_context(cx.context) IS NOT TRUE
       OR op.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')))))""",
    )
    value = _once(
        value,
        "OR (rr.engine='rdagent' AND mineagent_rd_source_context(cx.context) IS NOT TRUE)))",
        """OR (rr.engine='rdagent' AND mineagent_rd_source_context(cx.context) IS NOT TRUE)
    OR (rr.engine='openevolve' AND (mineagent_oe_context(cx.context) IS NOT TRUE
       OR cx.context->>'owner_id' IS DISTINCT FROM cx.owner_id::text
       OR cx.context->>'run_id' IS DISTINCT FROM cx.run_id::text
       OR cx.context_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(cx.context,false),'UTF8')),'hex')))))""",
    )
    value = _once(
        value,
        """(rr.engine<>'rdagent' OR br.amounts<>'{"calls":0,"tokens":0,"cost_microunits":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}'::jsonb
     OR NOT EXISTS(SELECT 1 FROM factor_generation_starts st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation))))""",
        """(br.amounts IS DISTINCT FROM '{"calls":0,"tokens":0,"cost_microunits":0,"wall_milliseconds":0,"storage_bytes":0,"mutation_slots":1,"evaluator_executions":0,"process_slots":0}'::jsonb
     OR NOT ((rr.engine='rdagent' AND EXISTS(
       SELECT 1 FROM factor_generation_starts st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)))
       OR (rr.engine='openevolve' AND EXISTS(
       SELECT 1 FROM search_iteration_intents st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation))))))""",
    )
    return _once(
        value, " RETURN NULL;\nEND $$;", OE_SPEC_CHECK + "\n RETURN NULL;\nEND $$;"
    )


def controller_consistency_sql():
    return _once(
        _safe_expressions(PRIOR_RD_CONTROLLER),
        "(child_admission.profile<>'mineagent.rdagent-orchestrator.v2' OR child_admission.role<>'rd_orchestrator'))",
        """NOT ((child_admission.profile='mineagent.rdagent-orchestrator.v2' AND child_admission.role='rd_orchestrator')
       OR (child_admission.profile='mineagent.openevolve-orchestrator.v1' AND child_admission.role='openevolve_orchestrator'
           AND child_admission.schema_version='mineagent.process-admission.v2' AND child_admission.ipc_policy='host_ticket'
           AND child_admission.ticket_sha256 ~ '^[a-f0-9]{64}$')))""",
    )


OE_SPEC_CHECK = r"""
 IF EXISTS(SELECT 1 FROM research_object_publications p JOIN research_runs rr ON rr.id=p.run_id
   WHERE p.owner_id=owner_uuid AND p.object_kind IN (@OE_KINDS@) AND rr.engine<>'openevolve')
 THEN RAISE EXCEPTION 'OE object publication requires exact search run'; END IF;
 FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid AND engine='openevolve' LOOP
  SELECT * INTO spec FROM strategy_search_specs WHERE run_id=r.id;
  IF NOT FOUND OR spec.owner_id IS DISTINCT FROM r.owner_id OR spec.spec IS DISTINCT FROM r.config
   OR spec.spec_sha256 IS DISTINCT FROM r.request_sha256
   OR spec.spec_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(spec.spec,false),'UTF8')),'hex')
   OR (spec.original_strategy_id::text,spec.original_source_sha256,spec.prepared_strategy_id::text,spec.prepared_source_sha256,
       spec.dataset_id::text,spec.dataset_sha256)
   IS DISTINCT FROM (r.config#>>'{preparation,original,strategy_version_id}',r.config#>>'{preparation,original,source_sha256}',
       r.config#>>'{preparation,prepared,strategy_version_id}',r.config#>>'{preparation,prepared,source_sha256}',
       r.config#>>'{execution,dataset_version_id}',r.config#>>'{execution,dataset_sha256}')
   OR spec.objective_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(r.config->'objective',false),'UTF8')),'hex')
   OR spec.execution_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(r.config->'execution',false),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'OE immutable spec/run agreement'; END IF;
  SELECT * INTO b FROM research_budget_controls WHERE run_id=r.id;
  IF NOT FOUND OR b.owner_id IS DISTINCT FROM r.owner_id OR b.limits IS DISTINCT FROM r.config->'budget'
  THEN RAISE EXCEPTION 'OE immutable global budget differs'; END IF;
 END LOOP;
"""


RUN_SQL = r"""
ALTER TABLE @S@.research_runs DROP CONSTRAINT ck_research_run_identity,
 DROP CONSTRAINT ck_research_run_legacy_config,
 ADD CONSTRAINT ck_research_run_identity CHECK (((purpose='legacy_strategy' AND engine='legacy')
 OR (purpose='factor_research' AND engine='rdagent') OR (purpose='strategy_search' AND engine='openevolve')) IS TRUE),
 ADD CONSTRAINT ck_research_run_legacy_config CHECK (((purpose='legacy_strategy' AND engine='legacy' AND strategy_version_id IS NOT NULL
 AND @S@.mineagent_legacy_run_config(config) IS TRUE) OR
 (purpose='factor_research' AND engine='rdagent' AND strategy_version_id IS NULL
 AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL
 AND submission_binding IS NULL AND portable_policy_image_digest IS NULL
 AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL
 AND @S@.mineagent_rd_source_config(config) IS TRUE
 AND dataset_version_id::text=config->'input'->'dataset'->>'dataset_version_id'
 AND request_sha256=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(config,true),'UTF8')),'hex')
 AND (research_image_digest IS NULL OR research_image_digest=config->'runtime'->>'orchestrator_image')) OR
 (purpose='strategy_search' AND engine='openevolve' AND strategy_version_id IS NOT NULL
 AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL
 AND submission_binding IS NULL AND portable_policy_image_digest IS NULL
 AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL
 AND @S@.mineagent_oe_source_config(config) IS TRUE
 AND owner_id::text=config->>'owner_id' AND id::text=config->>'run_id'
 AND strategy_version_id::text=config#>>'{preparation,prepared,strategy_version_id}'
 AND dataset_version_id::text=config#>>'{execution,dataset_version_id}'
 AND request_sha256=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(config,false),'UTF8')),'hex')
 AND (research_image_digest IS NULL OR research_image_digest=config->>'runtime_image'))) IS TRUE);
"""


MODEL_SQL = r"""
ALTER TABLE @S@.research_model_operations DROP CONSTRAINT ck_model_versioned_request,
 ADD CONSTRAINT ck_model_versioned_request CHECK (((protocol='mineagent.neutral-model.v1' AND @S@.mineagent_broker_request(request) IS TRUE)
 OR (protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1') AND @S@.mineagent_rd_source_request(request,protocol) IS TRUE)
 OR (protocol='mineagent.openevolve-chat.v1' AND @S@.mineagent_oe_request(request,protocol) IS TRUE)) IS TRUE);
ALTER TABLE @S@.research_operation_contexts DROP CONSTRAINT ck_versioned_operation_context,
 ADD CONSTRAINT ck_versioned_operation_context CHECK (((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal']
 AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1'
 AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number'
 AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE
 OR @S@.mineagent_rd_source_context(context) IS TRUE OR @S@.mineagent_oe_context(context) IS TRUE));
"""


PROFILE_SQL = r"""
ALTER TABLE @S@.research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK ((schema_version='mineagent.process-admission.v1' OR
 (schema_version='mineagent.process-admission.v2' AND ipc_policy='none' AND ticket_sha256 IS NULL AND (
 (role='factor_cutoff_candidate' AND profile='mineagent.factor-prefix-candidate.v2') OR
 (role='factor_normalizer' AND profile='mineagent.factor-numeric-hdf.v1') OR
 (role='native_evaluator' AND profile IN ('mineagent.qlib-direct-analysis.v1','mineagent.openevolve-native-evaluator.v1')))) OR
 (schema_version='mineagent.process-admission.v2' AND ipc_policy='host_ticket' AND ticket_sha256 ~ '^[a-f0-9]{64}$' AND (
 (role='rd_orchestrator' AND profile IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')) OR
 (role='openevolve_orchestrator' AND profile='mineagent.openevolve-orchestrator.v1')))) IS TRUE);
"""


ORDER_SQL = r"""
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_controller_receipt_order() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE a record; protected boolean;
BEGIN
 SELECT * INTO STRICT a FROM research_process_admissions WHERE id=NEW.admission_id;
 protected:=((a.profile='mineagent.rdagent-orchestrator.v2' AND a.role='rd_orchestrator') OR
   (a.profile='mineagent.openevolve-orchestrator.v1' AND a.role='openevolve_orchestrator'
    AND a.schema_version='mineagent.process-admission.v2' AND a.ipc_policy='host_ticket'
    AND a.ticket_sha256 ~ '^[a-f0-9]{64}$')) IS TRUE;
 IF NEW.kind='controller_child' AND NOT protected
 THEN RAISE EXCEPTION 'Child receipt requires exact protected controller'; END IF;
 IF NOT protected THEN RETURN NEW; END IF;
 IF NEW.kind NOT IN ('launch_intent','launch_identity','controller_child','stop')
 THEN RAISE EXCEPTION 'Protected controller cannot publish extraction'; END IF;
 IF EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='stop')
 THEN RAISE EXCEPTION 'Stopped controller receipt chain is closed'; END IF;
 IF NEW.kind<>'launch_intent' AND NOT EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_intent')
 THEN RAISE EXCEPTION 'Controller intent must precede physical identity'; END IF;
 IF NEW.kind IN ('controller_child','stop') AND NOT EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_identity')
 THEN RAISE EXCEPTION 'Protected leader identity must precede child or stop'; END IF;
 RETURN NEW;
END $$;
"""


LOCK_SQL = r"""
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_source_run_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE run_uuid uuid; actual_engine text; actual_owner uuid; supplied_owner uuid;
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN
   run_uuid:=(CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END);
 ELSE
   run_uuid:=(CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END);
 END IF;
 SELECT engine,owner_id INTO actual_engine,actual_owner FROM research_runs WHERE id=run_uuid;
 IF actual_engine IN ('rdagent','openevolve') THEN
   IF TG_TABLE_NAME<>'execution_attempts' THEN
     supplied_owner:=(CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END);
     IF supplied_owner IS DISTINCT FROM actual_owner THEN RAISE EXCEPTION 'Engine participant owner/run mismatch'; END IF;
   END IF;
   PERFORM 1 FROM research_runs WHERE id=run_uuid FOR UPDATE NOWAIT;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
"""


# Closed union, not a prefix/wildcard permission for future evidence kinds.
PRIOR_KINDS = (
    "model_request",
    "model_response",
    "process_receipt",
    "factor_response_assembly",
    "factor_generation_proposal",
    "factor_generation_feedback",
    "factor_adoption_receipt",
)
OE_KINDS = (
    "search_checkpoint_chunk",
    "search_checkpoint_file",
    "search_checkpoint_inventory",
    "search_evaluation_input",
    "search_source",
    "search_rendered_prompt",
    "search_proposal_source",
    "search_checkpoint_closure",
    "search_worker_identity",
    "search_population",
    "search_context",
    "search_controller_event",
    "search_worker_outcome",
    "search_native_observation",
    "search_native_failure",
    "search_objective",
)


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact SQL schema required")
    kinds = ",".join("'" + kind + "'" for kind in sorted((*PRIOR_KINDS, *OE_KINDS)))
    objects = "\n".join(
        f"ALTER TABLE @S@.{table} DROP CONSTRAINT ck_{table}_versioned_kind, ADD CONSTRAINT ck_{table}_versioned_kind CHECK (object_kind IN ({kinds}));"
        for table in ("research_objects", "research_object_publications")
    )
    triggers = "\n".join(
        f"CREATE CONSTRAINT TRIGGER oe_shared_semantics AFTER INSERT OR UPDATE OR DELETE ON @S@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @S@.mineagent_rd_shared_consistent();"
        for table in ("strategy_search_specs", "search_iteration_intents")
    )
    parts = (
        RUN_SQL,
        MODEL_SQL,
        PROFILE_SQL,
        objects,
        LOCK_SQL,
        ORDER_SQL,
        controller_consistency_sql(),
        shared_consistency_sql(),
        triggers,
    )
    return (
        "\n".join(parts)
        .replace("@S@", schema)
        .replace("@OE_KINDS@", ",".join("'" + kind + "'" for kind in OE_KINDS))
    )
