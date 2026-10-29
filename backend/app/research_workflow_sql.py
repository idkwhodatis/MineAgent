"""Explicit 028 finite-workflow relational guards, never physical-byte proof.

The assembler installs tables and RD/source fragments before these deferred
predicates. Host/cold readers still reconstruct immutable canonical bytes,
process/SDK containment, released measurements and actual first prompts.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_inputs.feedback import FeedbackProjectionDefinition
from packages.research_workflow.admission import (
    CreateWorkflow,
    SelectWorkflowInputs,
    WorkflowCreated,
)
from packages.research_workflow.contracts import (
    WorkflowCommand,
    WorkflowCommandReceipt,
    WorkflowInputSnapshot,
)
from packages.research_workflow.feedback_checkpoint import FeedbackCheckpoint
from packages.research_workflow.local_checkpoint import LocalNormalizationCheckpoint
from packages.research_workflow.plan import RDGuidancePlan
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.rd_claim import WorkflowRDClaim, WorkflowRDClaimReceipt
from packages.research_workflow.rd_stage import RDStageDefinition

TABLES = (
    "research_workflows",
    "research_workflow_revisions",
    "research_workflow_stages",
    "research_workflow_checkpoints",
    "research_workflow_commands",
    "research_workflow_model_consumption",
)
CONTRACTS = {
    "plan": RDGuidancePlan,
    "create": CreateWorkflow,
    "created": WorkflowCreated,
    "inputs": WorkflowInputSnapshot,
    "rd_stage": RDStageDefinition,
    "external_stage": ExternalNormalizationDefinition,
    "feedback_stage": FeedbackProjectionDefinition,
    "rd_checkpoint": RDStageCheckpoint,
    "external_checkpoint": LocalNormalizationCheckpoint,
    "feedback_checkpoint": FeedbackCheckpoint,
    "command": WorkflowCommand,
    "selection": SelectWorkflowInputs,
    "claim": WorkflowRDClaim,
    "command_receipt": WorkflowCommandReceipt,
    "claim_receipt": WorkflowRDClaimReceipt,
}


def render_upgrade_sql():
    rules = []
    for kind, model in CONTRACTS.items():
        schema = _schema(model)
        _require_serialized_keys(schema)
        if kind in {"plan", "create"}:
            # The optional controller selector is omitted in legacy canonical
            # JSON. The exact-object SQL grammar cannot represent optional keys:
            # validate it separately, then project only this field for shape.
            node = schema["$defs"]["WorkflowProviderResearchTemplate"]
            field = node["properties"].pop("controller_profile")
            if field.get("anyOf") != [
                {"const": "mineagent.rdagent-orchestrator.v3", "type": "string"},
                {"type": "null"},
            ]:
                raise ValueError("Exact optional controller selector required")
            node["required"].remove("controller_profile")
        raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        rules.append(f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;")
    sql = SQL.replace("@RULES@", "\n".join(rules))
    for table in TABLES:
        sql += f"\nCREATE TRIGGER workflow_before BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_workflow_before();\n"
        sql += f"CREATE CONSTRAINT TRIGGER workflow_graph AFTER INSERT OR UPDATE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_workflow_check();\n"
    return sql


def render_downgrade_sql():
    sql = "DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.research_workflows) THEN RAISE EXCEPTION 'Retained workflows block downgrade'; END IF; END $$;\n"
    for table in TABLES:
        sql += f"DROP TRIGGER workflow_graph ON @SCHEMA@.{table};\nDROP TRIGGER workflow_before ON @SCHEMA@.{table};\n"
    for signature in (
        "mineagent_workflow_check()",
        "mineagent_workflow_before()",
        "mineagent_workflow_consistent(uuid,uuid)",
        "mineagent_workflow_checkpoint(uuid,uuid,uuid)",
        "mineagent_workflow_refs(uuid,jsonb)",
        "mineagent_workflow_lock(uuid,uuid)",
        "mineagent_workflow_raw(text,text,text)",
        "mineagent_workflow_payload(text,jsonb)",
    ):
        sql += f"DROP FUNCTION @SCHEMA@.{signature};\n"
    return sql


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_workflow_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rule jsonb; cfg jsonb; path text[];
BEGIN
 IF kind IN ('plan','create') THEN
  path:=CASE WHEN kind='plan' THEN ARRAY['research_template'] ELSE ARRAY['plan','research_template'] END;
  cfg:=v#>path;
  IF jsonb_typeof(cfg) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
  IF cfg ? 'controller_profile' THEN
   IF cfg->'controller_profile' IS DISTINCT FROM '"mineagent.rdagent-orchestrator.v3"'::jsonb
    OR cfg->'max_iterations' IS DISTINCT FROM '1'::jsonb
    OR (cfg#>>'{budget,wall_milliseconds}')::numeric < 300000+32*(cfg#>>'{model_binding,request_deadline_ms}')::numeric
   THEN RETURN false; END IF;
  END IF;
  v:=jsonb_set(v,path,cfg-'controller_profile');
 END IF;
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_raw(raw text,sha text,kind text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF kind IN ('plan','create') THEN
  RETURN mineagent_workflow_payload(kind,raw::jsonb)
   AND mineagent_analysis_raw_commitment(raw,raw::jsonb,sha,1048576);
 END IF;
 RETURN raw IS NOT NULL AND sha=encode(sha256(convert_to(raw,'UTF8')),'hex')
  AND mineagent_workflow_payload(kind,raw::jsonb)
  AND mineagent_rd_canonical_json(raw::jsonb,true)=raw;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_lock(o uuid,w uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE k bigint;
BEGIN
 k:=('x'||substr(encode(sha256(convert_to('submit:'||o::text,'UTF8')),'hex'),1,16))::bit(64)::bigint;
 IF NOT pg_try_advisory_xact_lock(k) THEN RAISE EXCEPTION 'workflow owner is busy'; END IF;
 PERFORM 1 FROM research_workflows WHERE owner_id=o AND id=w FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'workflow owner scope missing'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_refs(o uuid,refs jsonb) RETURNS boolean
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r jsonb;
BEGIN
 IF jsonb_typeof(refs) IS DISTINCT FROM 'array' OR jsonb_array_length(refs)>32 THEN RETURN false; END IF;
 IF (SELECT count(DISTINCT (value->>'origin',value->>'record_id')) FROM jsonb_array_elements(refs))<>jsonb_array_length(refs)
 OR (SELECT count(DISTINCT (value->>'origin',value->>'version_id')) FROM jsonb_array_elements(refs))<>jsonb_array_length(refs) THEN RETURN false; END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(refs) LOOP
  IF r->>'origin'='manual_user_submission' THEN
   IF NOT EXISTS(SELECT 1 FROM rd_steer_versions v WHERE v.owner_id=o AND v.steer_id::text=r->>'record_id' AND v.id::text=r->>'version_id' AND v.content_sha256=r->>'content_sha256') THEN RETURN false; END IF;
  ELSIF r->>'origin' IN ('normalized_external_proposal','experiment_feedback_proposal') THEN
   IF NOT EXISTS(SELECT 1 FROM research_input_versions v JOIN research_input_records p ON p.id=v.record_id AND p.owner_id=v.owner_id WHERE v.owner_id=o AND p.origin=r->>'origin' AND p.id::text=r->>'record_id' AND v.id::text=r->>'version_id' AND v.content_sha256=r->>'content_sha256') THEN RETURN false; END IF;
  ELSE RETURN false;
  END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_checkpoint(o uuid,w uuid,cid uuid) RETURNS boolean
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE c record; s record; r record; v jsonb; kind text; e record; d record; obj jsonb; rid uuid; aid uuid; gen integer;
BEGIN
 SELECT * INTO c FROM research_workflow_checkpoints WHERE owner_id=o AND workflow_id=w AND id=cid;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO s FROM research_workflow_stages WHERE owner_id=o AND workflow_id=w AND id=c.stage_id;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO r FROM research_workflow_revisions WHERE owner_id=o AND workflow_id=w AND id=s.input_revision_id;
 IF NOT FOUND THEN RETURN false; END IF;
 kind:=CASE s.kind WHEN 'rd_factor_generation' THEN 'rd_checkpoint' WHEN 'external_normalization' THEN 'external_checkpoint' WHEN 'experiment_feedback_projection' THEN 'feedback_checkpoint' END;
 IF mineagent_workflow_raw(c.terminal_canonical,c.terminal_sha256,kind) IS NOT TRUE THEN RETURN false; END IF;
 v:=c.terminal_canonical::jsonb;
 IF (v->>'owner_id',v->>'workflow_id',v->>'stage_id',v->>'input_revision_id',v->>'input_sha256',v->>'definition_sha256',v->>'allocation_sha256') IS DISTINCT FROM (o::text,w::text,s.id::text,r.id::text,r.inputs_sha256,s.definition_sha256,s.allocation_sha256) THEN RETURN false; END IF;
 IF s.kind='rd_factor_generation' THEN
  rid:=(v->>'child_run_id')::uuid; aid:=(v->>'attempt_id')::uuid; gen:=(v->>'generation')::integer;
  IF rid IS DISTINCT FROM s.child_run_id OR NOT EXISTS(SELECT 1 FROM research_runs x JOIN execution_attempts a ON a.run_id=x.id WHERE x.owner_id=o AND x.id=rid AND x.status='succeeded' AND x.engine='rdagent' AND x.purpose='factor_research' AND x.active_attempt_id=aid AND x.generation=gen AND a.id=aid AND a.generation=gen AND a.finished_at IS NOT NULL AND a.sandbox_stopped IS TRUE) THEN RETURN false; END IF;
  IF (SELECT count(*) FROM execution_attempts WHERE run_id=rid)<>1 THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM factor_workflow_submissions f JOIN factor_generation_starts g ON g.owner_id=f.owner_id AND g.run_id=f.run_id AND g.spec_id=f.spec_id JOIN factor_generation_sources src ON src.owner_id=g.owner_id AND src.run_id=g.run_id AND src.start_id=g.id JOIN factor_evaluation_starts ev ON ev.owner_id=g.owner_id AND ev.run_id=g.run_id AND ev.start_id=g.id JOIN factor_evaluation_outcomes eo ON eo.evaluation_id=ev.id JOIN factor_execution_snapshot_publications snap ON snap.owner_id=g.owner_id AND snap.run_id=g.run_id AND snap.evaluation_id=ev.id JOIN factor_direct_analysis_intents ai ON ai.owner_id=f.owner_id AND ai.run_id=f.run_id AND ai.plan_id=f.plan_id AND ai.snapshot_id=snap.id JOIN factor_direct_analysis_outcomes ao ON ao.analysis_id=ai.id
   WHERE f.owner_id=o AND f.workflow_id=w AND f.stage_id=s.id AND f.run_id=rid AND f.id::text=v->>'submission_id' AND f.spec_id::text=v->>'spec_id' AND f.plan_id::text=v->>'plan_id'
    AND g.id::text=v->>'start_id' AND g.attempt_id=aid AND g.generation=gen AND g.logical_step=0 AND g.task_index=0
    AND src.id::text=v->>'source_id' AND src.attempt_id=aid AND src.generation=gen AND src.factor_version_id::text=v->>'factor_version_id' AND src.source_sha256=v->>'source_sha256'
    AND ev.id::text=v->>'evaluation_id' AND ev.evaluated_source_id=src.id AND ev.factor_version_id=src.factor_version_id AND eo.status='valid'
    AND snap.id::text=v->>'snapshot_id' AND snap.source_id=src.id AND snap.factor_version_id=src.factor_version_id AND snap.manifest_sha256=v->>'snapshot_manifest_sha256'
    AND ai.id::text=v->>'analysis_id' AND ao.result_sha256=v->>'analysis_result_sha256' AND ao.measurement_sha256=v->>'analysis_measurement_sha256') THEN RETURN false; END IF;
  IF (SELECT count(*) FROM research_process_admissions WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen)<>jsonb_array_length(v->'process_admission_ids')
   OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(v->'process_admission_ids'))<>jsonb_array_length(v->'process_admission_ids') THEN RETURN false; END IF;
  IF EXISTS(SELECT 1 FROM research_process_admissions p WHERE p.owner_id=o AND p.run_id=rid AND p.attempt_id=aid AND p.generation=gen AND (NOT v->'process_admission_ids' @> jsonb_build_array(p.id::text) OR NOT EXISTS(SELECT 1 FROM research_process_states st JOIN research_process_receipts stop ON stop.admission_id=st.admission_id AND stop.kind='stop' WHERE st.admission_id=p.id AND st.status='stopped'))) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM factor_controller_launches l JOIN research_process_receipts stop ON stop.admission_id=l.admission_id AND stop.kind='stop' WHERE l.owner_id=o AND l.run_id=rid AND l.attempt_id=aid AND l.generation=gen AND l.admission_id::text=v->>'controller_admission_id' AND stop.payload_sha256=v->>'controller_stop_sha256') THEN RETURN false; END IF;
  IF (SELECT count(*) FROM research_budget_reservations WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen)<>jsonb_array_length(v->'reservation_ids') OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(v->'reservation_ids'))<>jsonb_array_length(v->'reservation_ids') THEN RETURN false; END IF;
  IF EXISTS(SELECT 1 FROM research_budget_reservations b WHERE b.owner_id=o AND b.run_id=rid AND b.attempt_id=aid AND b.generation=gen AND (b.state<>'settled' OR NOT v->'reservation_ids' @> jsonb_build_array(b.id::text) OR NOT EXISTS(SELECT 1 FROM research_budget_settlements st WHERE st.reservation_id=b.id))) THEN RETURN false; END IF;
  IF EXISTS(SELECT 1 FROM research_model_transports t JOIN research_budget_reservations b ON b.id=t.reservation_id LEFT JOIN research_budget_settlements z ON z.reservation_id=b.id WHERE t.owner_id=o AND t.run_id=rid AND (t.state NOT IN ('completed','failed','unknown') OR b.state<>'settled' OR z.id IS NULL OR z.outcome<>'conservative_unknown' OR z.charge IS DISTINCT FROM b.amounts OR (SELECT array_agg(kind ORDER BY kind) FROM rd_provider_transport_receipts WHERE transport_id=t.id) IS DISTINCT FROM ARRAY['intent','started','stopped']::varchar[])) THEN RETURN false; END IF;
 ELSE
  SELECT * INTO d FROM research_input_derivations WHERE owner_id=o AND workflow_id=w AND stage_id=s.id AND id::text=v->>'derivation_id';
  IF NOT FOUND OR d.operation<>s.kind THEN RETURN false; END IF;
  SELECT * INTO e FROM research_input_derivation_events WHERE derivation_id=d.id ORDER BY ordinal DESC LIMIT 1;
  IF NOT FOUND OR e.id::text IS DISTINCT FROM v->>'terminal_event_id' OR e.state IS DISTINCT FROM v->>'state' OR e.state NOT IN ('succeeded','rejected','failed','cancelled','interrupted') OR e.outputs_canonical::jsonb IS DISTINCT FROM v->'outputs' THEN RETURN false; END IF;
  -- Timestamp spelling is exactly the retained UTC Pydantic event spelling.
  IF v->>'terminal_event_sha256' IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(jsonb_build_object('id',e.id::text,'derivation_id',d.id::text,'ordinal',e.ordinal,'state',e.state,'error_code',e.error_code,'outputs',e.outputs_canonical::jsonb,'recorded_at',to_char(e.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||CASE WHEN to_char(e.created_at AT TIME ZONE 'UTC','US')='000000' THEN '' ELSE '.'||to_char(e.created_at AT TIME ZONE 'UTC','US') END||'Z'),true),'UTF8')),'hex') THEN RETURN false; END IF;
  FOR obj IN SELECT value FROM jsonb_array_elements(v->'objects') LOOP
   IF NOT EXISTS(SELECT 1 FROM research_input_objects x WHERE x.owner_id=o AND x.id::text=obj->>'id' AND x.kind=obj->>'kind' AND x.sha256=obj->>'sha256' AND x.size_bytes=(obj->>'size_bytes')::bigint) THEN RETURN false; END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(v->'outputs') WITH ORDINALITY AS refs(value,ord) WHERE NOT EXISTS(SELECT 1 FROM research_input_versions iv WHERE iv.owner_id=o AND iv.derivation_id=d.id AND iv.id::text=refs.value->>'version_id' AND iv.record_id::text=refs.value->>'record_id' AND iv.content_sha256=refs.value->>'content_sha256' AND iv.object_id::text=(v->'objects'->(CASE WHEN s.kind='external_normalization' THEN refs.ord::integer ELSE refs.ord::integer-1 END)->>'id'))) THEN RETURN false; END IF;
  IF s.kind='external_normalization' THEN
   IF jsonb_array_length(v->'objects')<>1+jsonb_array_length(v->'outputs') OR v->'objects'->0->>'id' IS DISTINCT FROM d.raw_object_id::text OR v->'objects'->0->>'kind' IS DISTINCT FROM 'raw_external' OR v->'objects'->0->>'sha256' IS DISTINCT FROM d.raw_object_sha256 THEN RETURN false; END IF;
  ELSE
   IF v->'source' IS DISTINCT FROM s.definition_canonical::jsonb->'source' THEN RETURN false; END IF;
   IF NOT EXISTS(SELECT 1 FROM research_workflow_checkpoints p JOIN research_workflow_stages ps ON ps.id=p.stage_id AND ps.owner_id=p.owner_id AND ps.workflow_id=p.workflow_id WHERE p.owner_id=o AND p.workflow_id=w AND p.id::text=v->>'rd_checkpoint_id' AND ps.ordinal=s.ordinal-1 AND ps.kind='rd_factor_generation' AND ps.id::text=v->'source'->>'stage_id' AND p.terminal_canonical::jsonb->>'child_run_id'=v->'source'->>'child_run_id' AND p.terminal_canonical::jsonb->>'analysis_id'=v->'source'->>'output_id' AND p.terminal_canonical::jsonb->>'analysis_result_sha256'=v->'source'->>'content_sha256') THEN RETURN false; END IF;
   IF e.state='succeeded' THEN
    IF jsonb_array_length(v->'outputs')<>1 OR jsonb_array_length(v->'objects')<>1 OR jsonb_array_length(v->'exposure_ids')<>2 OR v->'exposure_ids'->0=v->'exposure_ids'->1 THEN RETURN false; END IF;
    IF (SELECT count(*) FROM data_usage_exposures x WHERE x.owner_id=o AND x.run_id::text=v->'source'->>'child_run_id' AND x.purpose='engine_direct_analysis_release' AND x.id::text IN (v->'exposure_ids'->>0,v->'exposure_ids'->>1) AND x.source_key IN ('engine-result:direct_analysis:'||(v->'source'->>'output_id')||':factor:v1','engine-result:direct_analysis:'||(v->'source'->>'output_id')||':label:v1'))<>2 THEN RETURN false; END IF;
   ELSIF v->'objects'<>'[]'::jsonb OR v->'outputs'<>'[]'::jsonb OR v->'exposure_ids'<>'[]'::jsonb THEN RETURN false;
   END IF;
  END IF;
 END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""

SQL += r"""
CREATE FUNCTION @SCHEMA@.mineagent_workflow_consistent(o uuid,w uuid) RETURNS boolean
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE root record; rev record; s record; cp record; prior record; cmd record; m record; p jsonb; v jsonb; d jsonb; result jsonb; previous_refs jsonb; expected_refs jsonb; n integer:=0; offset_n integer; iterations integer; iteration_n integer; expected_kind text; k text; amount numeric; minimum numeric; zero_vector jsonb:='{"calls":0,"tokens":0,"cost_microunits":0,"mutation_slots":0,"evaluator_executions":0,"wall_milliseconds":0,"storage_bytes":0,"process_slots":0}'::jsonb;
BEGIN
 SELECT * INTO root FROM research_workflows WHERE owner_id=o AND id=w;
 IF NOT FOUND THEN RETURN false; END IF;
 IF mineagent_workflow_raw(root.plan_canonical,root.plan_sha256,'plan') IS NOT TRUE OR mineagent_workflow_raw(root.creation_canonical,root.creation_sha256,'create') IS NOT TRUE THEN RETURN false; END IF;
 p:=root.plan_canonical::jsonb; v:=root.creation_canonical::jsonb; result:=root.creation_result_canonical::jsonb;
 IF p->>'owner_id' IS DISTINCT FROM o::text OR p->>'mode' IS DISTINCT FROM root.mode OR v->'plan' IS DISTINCT FROM p OR v->>'idempotency_key' IS DISTINCT FROM root.creation_key
  OR mineagent_rd_canonical_json(result,true) IS DISTINCT FROM root.creation_result_canonical OR mineagent_workflow_payload('created',result) IS NOT TRUE OR result->>'workflow_id' IS DISTINCT FROM w::text OR result->>'request_sha256' IS DISTINCT FROM root.creation_sha256 OR result->>'revision'<>'1'
  OR p->'budget_ceiling' IS DISTINCT FROM root.budget_canonical::jsonb OR encode(sha256(convert_to(root.budget_canonical,'UTF8')),'hex') IS DISTINCT FROM root.budget_sha256
  OR mineagent_budget_vector(root.budget_canonical::jsonb) IS NOT TRUE OR mineagent_rd_provider_config(p->'research_template') IS NOT TRUE
  OR p->'research_template'->'steer_snapshot'->>'owner_id' IS DISTINCT FROM o::text OR p->'research_template'->'steer_snapshot'->'versions' IS DISTINCT FROM '[]'::jsonb OR p->'research_template'->>'max_iterations'<>'1'
  OR p->'research_template'->'model_binding'->'pricing'->>'status'<>'operator_declared' THEN RETURN false; END IF;
 iterations:=(p->>'maximum_iterations')::integer; offset_n:=CASE WHEN (p->>'normalize_external_first')::boolean THEN 1 ELSE 0 END;
 IF offset_n=1 AND (v->'external_definition' IS NULL OR v->'external_definition'='null'::jsonb) OR offset_n=0 AND v->'external_definition' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
 FOR k IN SELECT jsonb_object_keys(zero_vector) LOOP
  minimum:=(p->'research_template'->'budget'->>k)::numeric*iterations;
  IF k='storage_bytes' THEN minimum:=minimum+65536*(iterations-1)+294912*offset_n; END IF;
  IF (root.budget_canonical::jsonb->>k)::numeric<minimum THEN RETURN false; END IF;
 END LOOP;
 FOR rev IN SELECT * FROM research_workflow_revisions WHERE owner_id=o AND workflow_id=w LOOP
  IF rev.ordinal>root.revision OR mineagent_workflow_raw(rev.inputs_canonical,rev.inputs_sha256,'inputs') IS NOT TRUE THEN RETURN false; END IF;
  v:=rev.inputs_canonical::jsonb;
  IF (v->>'owner_id',v->>'workflow_id',v->>'revision_id') IS DISTINCT FROM (o::text,w::text,rev.id::text) OR mineagent_workflow_refs(o,v->'refs') IS NOT TRUE THEN RETURN false; END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM research_workflow_revisions x WHERE x.owner_id=o AND x.workflow_id=w AND x.id=root.current_input_revision_id AND x.ordinal=(SELECT max(ordinal) FROM research_workflow_revisions WHERE owner_id=o AND workflow_id=w)) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM research_workflow_revisions x WHERE x.owner_id=o AND x.workflow_id=w AND x.ordinal=1 AND x.id::text=result->>'input_revision_id' AND x.inputs_canonical::jsonb->'refs'=root.creation_canonical::jsonb->'refs') THEN RETURN false; END IF;
 FOR s IN SELECT * FROM research_workflow_stages WHERE owner_id=o AND workflow_id=w ORDER BY ordinal LOOP
  n:=n+1;
  IF n>2*iterations-1+offset_n OR s.ordinal<>n THEN RETURN false; END IF;
  expected_kind:=CASE WHEN offset_n=1 AND n=1 THEN 'external_normalization' WHEN mod(n-offset_n,2)=1 THEN 'rd_factor_generation' ELSE 'experiment_feedback_projection' END;
  IF s.kind<>expected_kind OR encode(sha256(convert_to(s.allocation_canonical,'UTF8')),'hex')<>s.allocation_sha256 OR mineagent_budget_vector(s.allocation_canonical::jsonb) IS NOT TRUE THEN RETURN false; END IF;
  IF n>1 AND NOT EXISTS(SELECT 1 FROM research_workflow_stages ps JOIN research_workflow_checkpoints pc ON pc.stage_id=ps.id AND pc.owner_id=ps.owner_id AND pc.workflow_id=ps.workflow_id WHERE ps.owner_id=o AND ps.workflow_id=w AND ps.ordinal=n-1 AND (ps.kind='rd_factor_generation' OR pc.terminal_canonical::jsonb->>'state'='succeeded')) THEN RETURN false; END IF;
  IF s.kind='external_normalization' THEN
   IF mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'external_stage') IS NOT TRUE OR s.definition_canonical::jsonb IS DISTINCT FROM root.creation_canonical::jsonb->'external_definition' OR s.child_run_id IS NOT NULL OR s.allocation_canonical::jsonb IS DISTINCT FROM jsonb_set(zero_vector,'{storage_bytes}','294912') THEN RETURN false; END IF;
  ELSIF s.kind='experiment_feedback_projection' THEN
   IF mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'feedback_stage') IS NOT TRUE OR s.child_run_id IS NOT NULL OR s.allocation_canonical::jsonb IS DISTINCT FROM jsonb_set(zero_vector,'{storage_bytes}','65536') THEN RETURN false; END IF;
   d:=s.definition_canonical::jsonb;
   IF NOT EXISTS(SELECT 1 FROM research_workflow_stages ps JOIN research_workflow_checkpoints pc ON pc.owner_id=ps.owner_id AND pc.workflow_id=ps.workflow_id AND pc.stage_id=ps.id WHERE ps.owner_id=o AND ps.workflow_id=w AND ps.ordinal=n-1 AND ps.kind='rd_factor_generation' AND d->'source'=jsonb_build_object('workflow_id',w::text,'stage_id',ps.id::text,'child_run_id',ps.child_run_id::text,'output_kind','direct_analysis','output_id',pc.terminal_canonical::jsonb->>'analysis_id','content_sha256',pc.terminal_canonical::jsonb->>'analysis_result_sha256')) THEN RETURN false; END IF;
  ELSE
   IF mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'rd_stage') IS NOT TRUE OR s.child_run_id IS NULL OR s.allocation_canonical::jsonb IS DISTINCT FROM p->'research_template'->'budget' THEN RETURN false; END IF;
   d:=s.definition_canonical::jsonb; iteration_n:=(n-offset_n-1)/2;
   IF d->>'plan_sha256' IS DISTINCT FROM root.plan_sha256 OR (d->>'research_iteration')::integer<>iteration_n THEN RETURN false; END IF;
   IF iteration_n=0 THEN
    IF d->'parent_factor_version_id' IS DISTINCT FROM p->'initial_parent_factor_version_id' OR d->'parent_source_stage_id' IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
    IF offset_n=0 AND d->'predecessor_checkpoint_id' IS DISTINCT FROM 'null'::jsonb OR offset_n=1 AND NOT EXISTS(SELECT 1 FROM research_workflow_checkpoints c JOIN research_workflow_stages ps ON ps.id=c.stage_id WHERE c.owner_id=o AND c.workflow_id=w AND ps.ordinal=1 AND c.id::text=d->>'predecessor_checkpoint_id') THEN RETURN false; END IF;
   ELSE
    SELECT c.* INTO cp FROM research_workflow_checkpoints c JOIN research_workflow_stages ps ON ps.owner_id=c.owner_id AND ps.workflow_id=c.workflow_id AND ps.id=c.stage_id WHERE c.owner_id=o AND c.workflow_id=w AND ps.ordinal=n-1 AND c.id::text=d->>'predecessor_checkpoint_id';
    IF NOT FOUND OR cp.terminal_canonical::jsonb->>'state'<>'succeeded' THEN RETURN false; END IF;
    SELECT c.* INTO prior FROM research_workflow_checkpoints c JOIN research_workflow_stages ps ON ps.owner_id=c.owner_id AND ps.workflow_id=c.workflow_id AND ps.id=c.stage_id WHERE c.owner_id=o AND c.workflow_id=w AND ps.ordinal=n-2 AND ps.id::text=d->>'parent_source_stage_id' AND c.id::text=cp.terminal_canonical::jsonb->>'rd_checkpoint_id';
    IF NOT FOUND OR prior.terminal_canonical::jsonb->>'factor_version_id' IS DISTINCT FROM d->>'parent_factor_version_id' THEN RETURN false; END IF;
   END IF;
   IF (SELECT count(*) FROM factor_workflow_submissions f WHERE f.owner_id=o AND f.workflow_id=w AND f.stage_id=s.id AND f.run_id=s.child_run_id AND f.input_revision_id=s.input_revision_id AND f.definition_sha256=s.definition_sha256)<>1 THEN RETURN false; END IF;
   IF (SELECT count(*) FROM research_workflow_commands c WHERE c.owner_id=o AND c.workflow_id=w AND c.command_canonical::jsonb->>'schema_version'='mineagent.workflow-rd-claim.v1' AND c.result_canonical::jsonb->>'stage_id'=s.id::text AND c.result_canonical::jsonb->>'child_run_id'=s.child_run_id::text)<>1 THEN RETURN false; END IF;
   IF iteration_n>0 THEN
    SELECT * INTO cmd FROM research_workflow_commands c WHERE c.owner_id=o AND c.workflow_id=w AND c.result_canonical::jsonb->>'stage_id'=s.id::text AND c.command_canonical::jsonb->>'schema_version'='mineagent.workflow-rd-claim.v1';
    SELECT inputs_canonical::jsonb->'refs' INTO previous_refs FROM research_workflow_revisions x WHERE x.owner_id=o AND x.workflow_id=w AND x.id::text=cmd.command_canonical::jsonb->>'input_revision_id';
    IF NOT FOUND THEN RETURN false; END IF;
    SELECT coalesce(jsonb_agg(value ORDER BY ord),'[]'::jsonb) INTO expected_refs FROM jsonb_array_elements(previous_refs) WITH ORDINALITY AS refs(value,ord) WHERE value->>'origin'<>'experiment_feedback_proposal';
    expected_refs:=expected_refs||(cp.terminal_canonical::jsonb->'outputs');
    IF NOT EXISTS(SELECT 1 FROM research_workflow_revisions x WHERE x.owner_id=o AND x.workflow_id=w AND x.id=s.input_revision_id AND x.id::text<>cmd.command_canonical::jsonb->>'input_revision_id' AND x.inputs_canonical::jsonb->'refs'=expected_refs) THEN RETURN false; END IF;
   END IF;
  END IF;
 END LOOP;
 IF n=0 THEN
  IF root.current_stage_id IS NOT NULL OR root.latest_checkpoint_id IS NOT NULL OR root.status IN ('running','pause_requested','cancel_requested','completed') THEN RETURN false; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM research_workflow_stages sql_s WHERE sql_s.owner_id=o AND sql_s.workflow_id=w AND sql_s.ordinal=n AND sql_s.id=root.current_stage_id) THEN RETURN false; END IF;
 END IF;
 FOR k IN SELECT jsonb_object_keys(zero_vector) LOOP
  SELECT coalesce(sum((allocation_canonical::jsonb->>k)::numeric),0) INTO amount FROM research_workflow_stages WHERE owner_id=o AND workflow_id=w;
  IF amount>(root.budget_canonical::jsonb->>k)::numeric THEN RETURN false; END IF;
 END LOOP;
 FOR cp IN SELECT * FROM research_workflow_checkpoints WHERE owner_id=o AND workflow_id=w LOOP
  IF mineagent_workflow_checkpoint(o,w,cp.id) IS NOT TRUE THEN RETURN false; END IF;
 END LOOP;
 IF root.latest_checkpoint_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM research_workflow_checkpoints c JOIN research_workflow_stages sql_s ON sql_s.id=c.stage_id WHERE c.owner_id=o AND c.workflow_id=w AND c.id=root.latest_checkpoint_id AND sql_s.ordinal=(SELECT max(ps.ordinal) FROM research_workflow_checkpoints pc JOIN research_workflow_stages ps ON ps.id=pc.stage_id WHERE pc.owner_id=o AND pc.workflow_id=w)) THEN RETURN false; END IF;
 IF root.latest_checkpoint_id IS NULL AND EXISTS(SELECT 1 FROM research_workflow_checkpoints WHERE owner_id=o AND workflow_id=w) THEN RETURN false; END IF;
 IF n>0 AND root.status IN ('paused','cancelled','completed') AND NOT EXISTS(SELECT 1 FROM research_workflow_checkpoints WHERE owner_id=o AND workflow_id=w AND id=root.latest_checkpoint_id AND stage_id=root.current_stage_id) THEN RETURN false; END IF;
 IF root.status='completed' AND (n<>2*iterations-1+offset_n OR NOT EXISTS(SELECT 1 FROM research_workflow_stages WHERE owner_id=o AND workflow_id=w AND id=root.current_stage_id AND kind='rd_factor_generation')) THEN RETURN false; END IF;
 FOR cmd IN SELECT * FROM research_workflow_commands WHERE owner_id=o AND workflow_id=w LOOP
  v:=cmd.command_canonical::jsonb; result:=cmd.result_canonical::jsonb;
  expected_kind:=CASE v->>'schema_version' WHEN 'mineagent.workflow-command.v1' THEN 'command' WHEN 'mineagent.workflow-select-inputs.v1' THEN 'selection' WHEN 'mineagent.workflow-rd-claim.v1' THEN 'claim' END;
  IF mineagent_workflow_raw(cmd.command_canonical,cmd.command_sha256,expected_kind) IS NOT TRUE OR v->>'workflow_id' IS DISTINCT FROM w::text OR v->>'idempotency_key' IS DISTINCT FROM cmd.idempotency_key OR mineagent_workflow_payload(CASE WHEN expected_kind='claim' THEN 'claim_receipt' ELSE 'command_receipt' END,result) IS NOT TRUE OR (result->>'workflow_id',result->>'command_id',result->>'command_sha256') IS DISTINCT FROM (w::text,cmd.id::text,cmd.command_sha256) OR mineagent_rd_canonical_json(result,true) IS DISTINCT FROM cmd.result_canonical OR (result->>'revision')::integer>root.revision OR (result->>'revision')::integer<=(v->>'expected_revision')::integer THEN RETURN false; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM research_workflow_stages sql_s JOIN research_model_operations op ON op.owner_id=sql_s.owner_id AND op.run_id=sql_s.child_run_id JOIN factor_generation_model_operations member ON member.operation_id=op.id JOIN factor_upstream_calls call ON call.id=member.call_id WHERE sql_s.owner_id=o AND sql_s.workflow_id=w AND member.request_ordinal=0 AND member.relation='initial' AND call.phase='generation' AND call.ordinal=0 AND NOT EXISTS(SELECT 1 FROM research_workflow_model_consumption sql_m WHERE sql_m.owner_id=o AND sql_m.workflow_id=w AND sql_m.stage_id=sql_s.id AND sql_m.operation_id=op.id)) THEN RETURN false; END IF;
 FOR m IN SELECT * FROM research_workflow_model_consumption WHERE owner_id=o AND workflow_id=w LOOP
  IF NOT EXISTS(SELECT 1 FROM research_workflow_stages sql_s JOIN factor_workflow_submissions f ON f.owner_id=sql_s.owner_id AND f.workflow_id=sql_s.workflow_id AND f.stage_id=sql_s.id JOIN research_runs r ON r.id=f.run_id AND r.owner_id=f.owner_id JOIN research_model_operations op ON op.id=m.operation_id JOIN research_model_transports t ON t.id=m.transport_id JOIN factor_generation_model_operations member ON member.operation_id=op.id JOIN factor_upstream_calls call ON call.id=member.call_id WHERE sql_s.owner_id=o AND sql_s.workflow_id=w AND sql_s.id=m.stage_id AND sql_s.kind='rd_factor_generation' AND sql_s.child_run_id=m.run_id AND sql_s.input_revision_id=m.input_revision_id AND m.input_sha256=r.config->>'workflow_inputs_sha256' AND (op.owner_id,op.run_id,op.attempt_id,op.generation)=(m.owner_id,m.run_id,m.attempt_id,m.generation) AND op.request_sha256=m.request_sha256 AND (t.owner_id,t.run_id,t.attempt_id,t.generation,t.operation_id,t.ordinal)=(m.owner_id,m.run_id,m.attempt_id,m.generation,m.operation_id,1) AND (member.owner_id,member.run_id,member.attempt_id,member.generation)=(m.owner_id,m.run_id,m.attempt_id,m.generation) AND member.request_ordinal=0 AND member.relation='initial' AND call.phase='generation' AND call.ordinal=0) THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_before() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE o uuid; w uuid; k bigint;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Retained workflow rows cannot be deleted'; END IF;
 o:=NEW.owner_id;
 IF TG_TABLE_NAME='research_workflows' THEN
  w:=NEW.id;
  k:=('x'||substr(encode(sha256(convert_to('submit:'||o::text,'UTF8')),'hex'),1,16))::bit(64)::bigint;
  IF NOT pg_try_advisory_xact_lock(k) THEN RAISE EXCEPTION 'workflow owner is busy'; END IF;
  IF TG_OP='UPDATE' THEN
   IF (to_jsonb(NEW)-ARRAY['revision','status','blocked_reason','current_input_revision_id','current_stage_id','latest_checkpoint_id','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','status','blocked_reason','current_input_revision_id','current_stage_id','latest_checkpoint_id','updated_at']) OR NEW.revision<>OLD.revision+1 OR NEW.updated_at<OLD.updated_at OR OLD.status IN ('completed','cancelled') THEN RAISE EXCEPTION 'Invalid immutable workflow/CAS transition'; END IF;
  ELSIF NEW.revision<>1 THEN RAISE EXCEPTION 'Workflow must begin at revision one'; END IF;
 ELSE
  w:=NEW.workflow_id;
  IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Retained workflow child rows are immutable'; END IF;
  PERFORM mineagent_workflow_lock(o,w);
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_workflow_check() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE w uuid;
BEGIN
 IF TG_TABLE_NAME='research_workflows' THEN
  w:=NEW.id;
 ELSE
  w:=NEW.workflow_id;
 END IF;
 IF mineagent_workflow_consistent(NEW.owner_id,w) IS NOT TRUE THEN RAISE EXCEPTION 'Workflow immutable finite graph differs'; END IF;
 RETURN NULL;
END $$;
"""
