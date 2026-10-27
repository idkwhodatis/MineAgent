"""024 operational cross-row closure; SQL metadata is not physical authority.

Raw float-bearing bootstrap text is hashed as retained, never reconstructed from
JSONB. Python live/cold readers independently enforce canonical bytes and actual
objects. Deferred checks permit a charged incomplete upload/preparation, never
an invented completion. This module does not install or enable a profile.
"""

import json
import re

TABLES = (
    "search_controller_launches",
    "search_checkpoint_uploads",
    "search_checkpoint_chunks",
    "search_checkpoint_upload_completions",
    "search_evaluation_inputs",
    "search_evaluation_preparations",
    "search_evaluations",
    "search_candidates",
    "strategy_search_epochs",
    "strategy_search_specs",
    "search_checkpoint_intents",
    "search_control_transitions",
    "search_checkpoint_publications",
    "research_process_admissions",
    "research_process_receipts",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_objects",
)


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact SQL schema required")
    from packages.openevolve_adapter.upload_contracts import Begin
    from runtimes.openevolve_operational.bootstrap import OperationalBootstrap
    from runtimes.openevolve_operational.profile import profile_sha256

    from backend.app.analysis_process_ddl import _require_serialized_keys
    from backend.app.rdagent_controller_ddl import _schema
    from packages.openevolve_adapter.runtime_profile import PROFILE

    rules = []
    for name, model in (("bootstrap", OperationalBootstrap), ("begin", Begin)):
        rule = _schema(model)
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        rules.append(f"WHEN '{name}' THEN rule:='{raw}'::jsonb;")
    sql = (
        SQL.replace("@S@", schema)
        .replace("@RULES@", "\n".join(rules))
        .replace("@PHYSICAL@", profile_sha256())
        .replace("@RUNTIME@", PROFILE.sha256)
    )
    return (
        sql
        + "\n"
        + "\n".join(
            f"CREATE CONSTRAINT TRIGGER oe024_operational_consistent AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_oe_operational_consistent();"
            for table in TABLES
        )
    )


SQL = r"""
CREATE FUNCTION @S@.mineagent_oe_operational_shape(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@S@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @S@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_operational_launch(lid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE l record; e record; s record; a record; p jsonb; b jsonb;
BEGIN
 SELECT * INTO l FROM search_controller_launches WHERE id=lid;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO e FROM strategy_search_epochs WHERE id=l.epoch_id;
 IF NOT FOUND OR (e.owner_id,e.run_id,e.attempt_id,e.generation,e.admission_id)
 IS DISTINCT FROM (l.owner_id,l.run_id,l.attempt_id,l.generation,l.admission_id) THEN RETURN false; END IF;
 SELECT * INTO s FROM strategy_search_specs WHERE run_id=l.run_id AND owner_id=l.owner_id;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO a FROM research_process_admissions WHERE id=l.admission_id;
 IF NOT FOUND OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.logical_operation_id)
 IS DISTINCT FROM (l.owner_id,l.run_id,l.attempt_id,l.generation,l.id)
 OR a.profile<>'mineagent.openevolve-orchestrator.v1' OR a.role<>'openevolve_orchestrator'
 OR a.ipc_policy<>'host_ticket' OR a.launch_ordinal<>1
 OR a.image_id IS DISTINCT FROM s.spec->>'runtime_image' THEN RETURN false; END IF;
 b:=l.bootstrap;
 IF @S@.mineagent_oe_operational_shape('bootstrap',b) IS NOT TRUE
 OR octet_length(l.bootstrap_canonical) NOT BETWEEN 2 AND 262144
 OR l.bootstrap_canonical::jsonb IS DISTINCT FROM b
 OR encode(sha256(convert_to(l.bootstrap_canonical,'UTF8')),'hex') IS DISTINCT FROM l.bootstrap_sha256
 OR (b->>'admission_id',b->>'epoch_id',b->>'config_sha256',b->>'physical_profile_sha256',b->>'runtime_profile_sha256')
 IS DISTINCT FROM (a.id::text,e.id::text,s.spec_sha256,'@PHYSICAL@','@RUNTIME@')
 OR b->'spec' IS DISTINCT FROM s.spec OR b->>'adapter_patch_sha256' IS DISTINCT FROM s.spec->>'adapter_patch_sha256'
 OR l.physical_profile_sha256 IS DISTINCT FROM '@PHYSICAL@'
 OR e.bootstrap_sha256 IS DISTINCT FROM l.input_inventory_sha256
 OR e.runtime_profile_sha256 IS DISTINCT FROM '@RUNTIME@'
 OR e.spec_sha256 IS DISTINCT FROM s.spec_sha256
 OR l.input_inventory_sha256 IS DISTINCT FROM a.input_sha256
 OR b#>>'{native_runtime,protected_image_id}' IS DISTINCT FROM s.spec#>>'{execution,evaluator_image}'
 OR b#>>'{native_runtime,profile}' IS DISTINCT FROM 'mineagent.openevolve-native-evaluator.v1'
 OR b#>>'{spec,preparation,kind}' IS DISTINCT FROM 'none'
 OR b#>>'{spec,factors,kind}' IS DISTINCT FROM 'none'
 OR b#>'{native_inputs,strategy_contract,parameters}' IS DISTINCT FROM '[]'::jsonb
 OR (b#>>'{spec,maximum_mutation_slots}')::integer NOT BETWEEN 1 AND 2
 OR (b#>>'{spec,population_size}')::integer>8 OR (b#>>'{spec,archive_size}')::integer>8
 THEN RETURN false; END IF;
 SELECT payload INTO p FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 IF NOT FOUND OR p->>'bootstrap_sha256' IS DISTINCT FROM l.bootstrap_sha256
 OR (p->>'bootstrap_size_bytes')::integer IS DISTINCT FROM octet_length(l.bootstrap_canonical)
 OR p->>'seed_sha256' IS DISTINCT FROM s.prepared_source_sha256
 OR p->>'issued_at' IS DISTINCT FROM b->>'issued_at'
 OR p->>'not_after' IS DISTINCT FROM b->>'not_after'
 OR (b->>'not_after')::timestamptz<=(b->>'issued_at')::timestamptz
 OR (b->>'not_after')::timestamptz>(b->>'issued_at')::timestamptz+interval '600 seconds'
 THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_operational_upload(uid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE u record; i record; b record; c record; o record; done record; control_row record;
 f jsonb; name text; previous text:=''; total bigint:=0; count_files integer:=0;
 count_chunks integer:=0; actual_chunks integer:=0; off integer; sz integer; peak bigint;
 expected_files text[]:=ARRAY[]::text[];
BEGIN
 SELECT * INTO u FROM search_checkpoint_uploads WHERE id=uid;
 IF NOT FOUND OR @S@.mineagent_oe_operational_shape('begin',u.inventory) IS NOT TRUE
 OR octet_length(u.inventory::text)>8192
 OR u.inventory->>'token' IS DISTINCT FROM u.checkpoint_intent_id::text
 OR u.inventory_sha256 IS DISTINCT FROM encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(u.inventory,false),'UTF8')),'hex') THEN RETURN false; END IF;
 SELECT * INTO i FROM search_checkpoint_intents WHERE id=u.checkpoint_intent_id;
 IF NOT FOUND OR (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id)
 IS DISTINCT FROM (u.owner_id,u.run_id,u.attempt_id,u.generation,u.epoch_id) THEN RETURN false; END IF;
 SELECT * INTO control_row FROM search_control_transitions WHERE id=i.control_transition_id;
 IF NOT FOUND OR (control_row.owner_id,control_row.run_id,control_row.attempt_id,control_row.generation,control_row.epoch_id,control_row.frontier,control_row.state_sha256)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id,i.frontier,i.state_sha256)
 OR control_row.sequence NOT BETWEEN 1 AND 5 THEN RETURN false; END IF;
 SELECT * INTO b FROM research_budget_reservations WHERE id=u.reservation_id;
 IF NOT FOUND OR (b.owner_id,b.run_id,b.attempt_id,b.generation)
 IS DISTINCT FROM (u.owner_id,u.run_id,u.attempt_id,u.generation)
 OR b.purpose<>'object_storage' OR b.logical_key IS DISTINCT FROM 'oe-upload:'||u.checkpoint_intent_id::text THEN RETURN false; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(u.inventory->'files') LOOP
  name:=f->>'name'; sz:=(f->>'size_bytes')::integer;
  IF name<=previous COLLATE "C" OR NOT (name IN ('metadata.json','best_program.py','best_program_info.json')
    OR name ~ '^programs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$') THEN RETURN false; END IF;
  previous:=name; expected_files:=array_append(expected_files,name);
  total:=total+sz; count_files:=count_files+1;
  FOR off IN SELECT generate_series(0,sz-1,65536) LOOP
   SELECT * INTO c FROM search_checkpoint_chunks WHERE upload_id=u.id AND sequence=count_chunks;
   IF FOUND THEN
    IF (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.file_name,c.offset_bytes,c.size_bytes)
     IS DISTINCT FROM (u.owner_id,u.run_id,u.attempt_id,u.generation,u.epoch_id,name,off,least(65536,sz-off))
     OR c.object_kind<>'search_checkpoint_chunk' THEN RETURN false; END IF;
    SELECT * INTO o FROM research_objects WHERE id=c.object_id AND owner_id=c.owner_id;
    IF NOT FOUND OR o.object_kind IS DISTINCT FROM c.object_kind OR o.sha256 IS DISTINCT FROM c.object_sha256 THEN RETURN false; END IF;
    actual_chunks:=actual_chunks+1;
    IF actual_chunks<>count_chunks+1 THEN RETURN false; END IF;
   END IF;
   count_chunks:=count_chunks+1;
  END LOOP;
 END LOOP;
 IF NOT expected_files @> ARRAY['metadata.json','best_program.py','best_program_info.json']
 OR (u.total_bytes,u.file_count,u.chunk_count) IS DISTINCT FROM (total,count_files,count_chunks)
 OR (SELECT count(*) FROM search_checkpoint_chunks WHERE upload_id=u.id)<>actual_chunks THEN RETURN false; END IF;
 peak:=2*total+2*1048576+2*4096*(count_files+count_chunks+1);
 IF b.amounts IS DISTINCT FROM jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'wall_milliseconds',0,
 'storage_bytes',peak,'mutation_slots',0,'evaluator_executions',0,'process_slots',0) THEN RETURN false; END IF;
 SELECT * INTO done FROM search_checkpoint_upload_completions WHERE upload_id=u.id;
 IF NOT FOUND THEN
  RETURN b.state IN ('reserved','uncertain') AND NOT EXISTS(SELECT 1 FROM research_budget_settlements WHERE reservation_id=b.id);
 END IF;
 IF actual_chunks<>count_chunks OR (done.owner_id,done.run_id,done.attempt_id,done.generation,done.epoch_id)
 IS DISTINCT FROM (u.owner_id,u.run_id,u.attempt_id,u.generation,u.epoch_id)
 OR done.inventory_kind<>'search_checkpoint_inventory' THEN RETURN false; END IF;
 SELECT * INTO o FROM research_objects WHERE id=done.inventory_object_id AND owner_id=u.owner_id;
 IF NOT FOUND OR o.object_kind IS DISTINCT FROM done.inventory_kind OR o.sha256 IS DISTINCT FROM done.inventory_sha256 THEN RETURN false; END IF;
 IF b.state='settled' AND (NOT EXISTS(SELECT 1 FROM research_budget_settlements z
  WHERE z.reservation_id=b.id AND (z.owner_id,z.run_id,z.attempt_id,z.generation)
  IS NOT DISTINCT FROM (b.owner_id,b.run_id,b.attempt_id,b.generation)
  AND z.outcome='conservative_unknown' AND z.charge=b.amounts)
  OR NOT EXISTS(SELECT 1 FROM search_controller_launches l WHERE l.epoch_id=u.epoch_id
   AND @S@.mineagent_oe_process_valid(l.admission_id,true) IS TRUE)
  OR NOT EXISTS(SELECT 1 FROM search_checkpoint_publications p WHERE p.intent_id=u.checkpoint_intent_id))
 THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_operational_evaluation(pid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE p record; c record; s record; l record; b record; e record; i record; a record; o record;
 intent jsonb; binding jsonb; window_count integer;
BEGIN
 SELECT * INTO p FROM search_evaluation_preparations WHERE id=pid;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO c FROM search_candidates WHERE id=p.candidate_id;
 IF NOT FOUND OR (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.source_sha256)
 IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.source_sha256) THEN RETURN false; END IF;
 SELECT * INTO s FROM strategy_search_specs WHERE run_id=p.run_id AND owner_id=p.owner_id;
 IF NOT FOUND OR p.execution_sha256 IS DISTINCT FROM s.execution_sha256 OR p.physical_ordinal<>1 THEN RETURN false; END IF;
 SELECT count(*) INTO window_count FROM jsonb_array_elements(s.spec#>'{execution,windows}') w WHERE w->>'id'=p.window_id;
 IF window_count<>1 THEN RETURN false; END IF;
 SELECT * INTO l FROM search_controller_launches WHERE epoch_id=p.epoch_id;
 IF NOT FOUND OR @S@.mineagent_oe_operational_launch(l.id) IS NOT TRUE THEN RETURN false; END IF;
 SELECT * INTO b FROM research_budget_reservations WHERE id=p.reservation_id;
 IF NOT FOUND OR (b.owner_id,b.run_id,b.attempt_id,b.generation)
 IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation)
 OR b.purpose<>'process_unit' OR b.logical_key IS DISTINCT FROM 'oe-native:'||p.id::text||':1'
 OR b.amounts IS DISTINCT FROM '{"calls":0,"tokens":0,"cost_microunits":0,"wall_milliseconds":90000,"storage_bytes":268500992,"mutation_slots":0,"evaluator_executions":1,"process_slots":1}'::jsonb THEN RETURN false; END IF;
 SELECT * INTO e FROM search_evaluations WHERE id=p.id;
 IF NOT FOUND THEN
  RETURN b.state IN ('reserved','uncertain') AND NOT EXISTS(SELECT 1 FROM research_budget_settlements WHERE reservation_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM research_process_admissions WHERE id=p.admission_id);
 END IF;
 IF (e.owner_id,e.run_id,e.attempt_id,e.generation,e.epoch_id,e.candidate_id,e.source_sha256,e.window_id,e.execution_sha256,e.logical_ticket_id,e.physical_ordinal,e.reservation_id,e.process_admission_id)
 IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.candidate_id,p.source_sha256,p.window_id,p.execution_sha256,p.logical_ticket_id,1,p.reservation_id,p.admission_id)
 OR e.phase IS DISTINCT FROM c.kind OR e.original_strategy_id IS NOT NULL OR e.prepared_strategy_id IS NOT NULL THEN RETURN false; END IF;
 SELECT * INTO i FROM search_evaluation_inputs WHERE evaluation_id=e.id;
 IF NOT FOUND OR (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id)
 IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id) THEN RETURN false; END IF;
 SELECT * INTO o FROM research_objects WHERE id=i.manifest_object_id AND owner_id=p.owner_id;
 IF NOT FOUND OR o.object_kind IS DISTINCT FROM 'search_evaluation_input' OR i.manifest_kind IS DISTINCT FROM o.object_kind
 OR o.sha256 IS DISTINCT FROM i.manifest_sha256 OR o.size_bytes IS DISTINCT FROM i.manifest_size_bytes THEN RETURN false; END IF;
 SELECT * INTO a FROM research_process_admissions WHERE id=p.admission_id;
 IF NOT FOUND OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.logical_operation_id,a.reservation_id)
 IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation,p.id,p.reservation_id)
 OR a.profile<>'mineagent.openevolve-native-evaluator.v1' OR a.role<>'native_evaluator'
 OR a.ipc_policy<>'none' OR a.ticket_sha256 IS NOT NULL OR a.launch_ordinal<>1
 OR a.image_id IS DISTINCT FROM l.bootstrap#>>'{native_runtime,protected_image_id}' THEN RETURN false; END IF;
 SELECT payload INTO intent FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 IF NOT FOUND THEN RETURN false; END IF;
 binding:=jsonb_build_object('schema_version','mineagent.openevolve-native-input-binding.v1',
 'evaluation_id',p.id,'manifest_object_id',i.manifest_object_id,'manifest_sha256',i.manifest_sha256,
 'manifest_size_bytes',i.manifest_size_bytes,'native_artifacts_sha256',e.input_manifest_sha256,
 'execution_plan_sha256',p.execution_sha256,'source_sha256',p.source_sha256);
 IF intent->'inputs' IS DISTINCT FROM binding OR intent->>'id' IS DISTINCT FROM a.id::text
 OR a.input_sha256 IS DISTINCT FROM encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(binding,true),'UTF8')),'hex')
 OR (intent->>'not_after')::timestamptz+interval '1 second'>(l.bootstrap->>'not_after')::timestamptz THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_operational_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE r record;
BEGIN
 FOR r IN SELECT id FROM search_controller_launches LOOP
  IF @S@.mineagent_oe_operational_launch(r.id) IS NOT TRUE THEN RAISE EXCEPTION 'OE operational launch closure'; END IF;
 END LOOP;
 FOR r IN SELECT id FROM search_checkpoint_uploads LOOP
  IF @S@.mineagent_oe_operational_upload(r.id) IS NOT TRUE THEN RAISE EXCEPTION 'OE operational upload closure'; END IF;
 END LOOP;
 FOR r IN SELECT id FROM search_evaluation_preparations LOOP
  IF @S@.mineagent_oe_operational_evaluation(r.id) IS NOT TRUE THEN RAISE EXCEPTION 'OE operational evaluation closure'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM research_process_admissions a WHERE
  (a.profile='mineagent.openevolve-orchestrator.v1' AND NOT EXISTS(
   SELECT 1 FROM search_controller_launches l WHERE l.admission_id=a.id AND l.id=a.logical_operation_id))
  OR (a.profile='mineagent.openevolve-native-evaluator.v1' AND NOT EXISTS(
   SELECT 1 FROM search_evaluation_preparations p JOIN search_evaluations e ON e.id=p.id
   WHERE p.admission_id=a.id AND p.id=a.logical_operation_id AND e.process_admission_id=a.id)))
 OR EXISTS(SELECT 1 FROM strategy_search_epochs e LEFT JOIN search_controller_launches l ON l.epoch_id=e.id WHERE l.id IS NULL)
 OR EXISTS(SELECT 1 FROM search_evaluations e LEFT JOIN search_evaluation_preparations p ON p.id=e.id WHERE p.id IS NULL)
 OR EXISTS(SELECT 1 FROM search_evaluation_inputs i LEFT JOIN search_evaluations e ON e.id=i.evaluation_id WHERE e.id IS NULL)
 OR EXISTS(SELECT 1 FROM search_checkpoint_intents WHERE save_ordinal IS NULL)
 OR EXISTS(SELECT 1 FROM search_checkpoint_intents GROUP BY epoch_id,save_ordinal HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM search_checkpoint_intents GROUP BY epoch_id
  HAVING min(save_ordinal)<>1 OR max(save_ordinal)>4 OR count(*)<>max(save_ordinal))
 OR EXISTS(SELECT 1 FROM search_checkpoint_publications p WHERE NOT EXISTS(
  SELECT 1 FROM search_checkpoint_uploads u JOIN search_checkpoint_upload_completions c ON c.upload_id=u.id
  WHERE u.checkpoint_intent_id=p.intent_id AND (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.inventory_object_id,c.inventory_kind,c.inventory_sha256)
  IS NOT DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.inventory_object_id,p.inventory_kind,p.inventory_sha256)))
 THEN RAISE EXCEPTION 'OE operational orphan or duplicate completion'; END IF;
 RETURN NULL;
END $$;
"""
