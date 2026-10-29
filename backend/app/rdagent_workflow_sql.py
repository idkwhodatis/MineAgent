"""028-only workflow child relational closure, never physical-file authority.

Install mapped workflow/receipt tables and canonical/schema helpers first. Shared
028 dispatch must explicitly call this predicate for v3; no old function is
replaced here. Host/cold readers still verify retained inputs, exact prompts,
predecessor physical checkpoints, model receipts, and actual numeric outputs.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

TABLES = (
    "factor_workflow_submissions",
    "factor_research_submissions",
    "factor_seeded_submissions",
    "research_runs",
    "factor_research_specs",
    "factor_analysis_plans",
    "outbox_events",
    "research_budget_controls",
    "factor_generation_starts",
    "factor_source_publications",
    "research_workflows",
    "research_workflow_stages",
    "research_workflow_revisions",
    "research_workflow_checkpoints",
    "factor_versions",
    "factor_objects",
)


def render_upgrade_sql():
    schema = _schema(WorkflowFactorResearchConfig)
    _require_serialized_keys(schema)
    # The only absent serialized field is the versioned prompt-layout opt-in.
    # Retained v3 configurations must keep their exact original bytes.
    schema["required"].remove("controller_profile")
    del schema["properties"]["controller_profile"]
    schema["required"].remove("workflow_prompt_layout")
    del schema["properties"]["workflow_prompt_layout"]
    raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return SQL.replace("@CONFIG_SCHEMA@", raw) + "\n".join(
        f"CREATE CONSTRAINT TRIGGER rd_workflow_submission_graph AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_workflow_consistent();"
        for table in TABLES
    )


def render_downgrade_sql():
    return (
        """
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_workflow_submissions)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.factor-research.v3')
 THEN RAISE EXCEPTION 'Workflow child history cannot be downgraded'; END IF;
END $$;
"""
        + "\n".join(
            f"DROP TRIGGER rd_workflow_submission_graph ON @SCHEMA@.{table};"
            for table in TABLES
        )
        + """
DROP TRIGGER rd_workflow_submission_append ON @SCHEMA@.factor_workflow_submissions;
DROP FUNCTION @SCHEMA@.mineagent_rd_workflow_append();
DROP FUNCTION @SCHEMA@.mineagent_rd_workflow_consistent();
DROP FUNCTION @SCHEMA@.mineagent_rd_workflow_submission_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_rd_workflow_config(jsonb);
"""
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_workflow_config(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rule jsonb:='@CONFIG_SCHEMA@'::jsonb; base jsonb; x jsonb; w jsonb; b jsonb; s jsonb;
BEGIN
 IF v ? 'controller_profile' AND v->'controller_profile' IS DISTINCT FROM '"mineagent.rdagent-orchestrator.v3"'::jsonb THEN RETURN false; END IF;
 IF mineagent_rd_controller_selection(v) IS NULL THEN RETURN false; END IF;
 IF v ? 'workflow_prompt_layout' AND v->'workflow_prompt_layout' IS DISTINCT FROM '"generation_user.v1"'::jsonb THEN RETURN false; END IF;
 IF mineagent_rd_schema(v-ARRAY['workflow_prompt_layout','controller_profile'],rule,coalesce(rule->'$defs','{}'::jsonb),0) IS NOT TRUE THEN RETURN false; END IF;
 base:=(v-ARRAY['workflow_child_run_id','workflow_inputs','workflow_inputs_sha256','workflow_parent','workflow_prompt_layout'])
  ||jsonb_build_object('schema_version','mineagent.factor-research.v2','model_profile','configured.rdagent.v1');
 IF mineagent_rd_provider_config(base) IS NOT TRUE THEN RETURN false; END IF;
 w:=v->'workflow_inputs'; b:=w->'binding'; s:=w->'selection';
 IF v->>'max_iterations' IS DISTINCT FROM '1'
 OR v#>'{steer_snapshot,versions}' IS DISTINCT FROM '[]'::jsonb
 OR v#>>'{steer_snapshot,owner_id}' IS DISTINCT FROM b->>'owner_id'
 OR octet_length(mineagent_rd_canonical_json(w,true))>32768
 OR v->>'workflow_inputs_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(w)
 OR b->>'input_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(s)
 OR (b->>'owner_id',b->>'workflow_id',b->>'input_revision_id')
  IS DISTINCT FROM (s->>'owner_id',s->>'workflow_id',s->>'revision_id')
 OR (v->'workflow_parent'<>'null'::jsonb AND v#>>'{workflow_parent,owner_id}' IS DISTINCT FROM b->>'owner_id')
 OR coalesce((SELECT jsonb_agg(value->'ref' ORDER BY ord) FROM jsonb_array_elements(w->'inputs') WITH ORDINALITY x(value,ord)),'[]'::jsonb)
  IS DISTINCT FROM s->'refs'
 OR (SELECT count(DISTINCT (value->>'origin',value->>'record_id')) FROM jsonb_array_elements(s->'refs'))<>jsonb_array_length(s->'refs')
 OR (SELECT count(DISTINCT (value->>'origin',value->>'version_id')) FROM jsonb_array_elements(s->'refs'))<>jsonb_array_length(s->'refs')
 THEN RETURN false; END IF;
 FOR x IN SELECT value FROM jsonb_array_elements(w->'inputs') LOOP
  IF x#>>'{ref,origin}'='unknown' OR length(btrim(x->>'title'))=0 OR length(btrim(x->>'text'))=0
  OR octet_length(x->>'title')>800 OR octet_length(x->>'text')>8192
  OR (x->>'title') ~ '[[:cntrl:]]'
  OR (x#>>'{ref,origin}'<>'manual_user_submission' AND translate(x->>'text',chr(9)||chr(10),'') ~ '[[:cntrl:]]')
  OR x->>'text_sha256' IS DISTINCT FROM encode(sha256(convert_to(x->>'text','UTF8')),'hex')
  THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_workflow_submission_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE q factor_workflow_submissions%ROWTYPE; r research_runs%ROWTYPE;
 sp factor_research_specs%ROWTYPE; p factor_analysis_plans%ROWTYPE;
 root research_workflows%ROWTYPE; st research_workflow_stages%ROWTYPE;
 rev research_workflow_revisions%ROWTYPE; prior research_workflow_stages%ROWTYPE;
 fb research_workflow_stages%ROWTYPE; fc research_workflow_checkpoints%ROWTYPE;
 rc research_workflow_checkpoints%ROWTYPE; fv factor_versions%ROWTYPE; obj factor_objects%ROWTYPE;
 cfg jsonb; bind jsonb; sel jsonb; def jsonb; plan jsonb; parent jsonb;
 expected jsonb; fields jsonb; exact_bytes text; profile_sha text; preflight jsonb;
BEGIN
 SELECT * INTO STRICT q FROM factor_workflow_submissions WHERE id=sid;
 SELECT * INTO STRICT r FROM research_runs WHERE id=q.run_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT sp FROM factor_research_specs WHERE id=q.spec_id AND owner_id=q.owner_id AND run_id=r.id;
 SELECT * INTO STRICT p FROM factor_analysis_plans WHERE id=q.plan_id AND owner_id=q.owner_id AND run_id=r.id;
 PERFORM mineagent_analysis_plan_check(p.id);
 PERFORM mineagent_rd_provider_queued_config(r.id);
 SELECT * INTO STRICT root FROM research_workflows WHERE id=q.workflow_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT st FROM research_workflow_stages WHERE id=q.stage_id AND owner_id=q.owner_id AND workflow_id=root.id;
 SELECT * INTO STRICT rev FROM research_workflow_revisions WHERE id=q.input_revision_id AND owner_id=q.owner_id AND workflow_id=root.id;
 cfg:=r.config; bind:=cfg#>'{workflow_inputs,binding}'; sel:=rev.inputs_canonical::jsonb;
 def:=st.definition_canonical::jsonb; plan:=root.plan_canonical::jsonb; parent:=cfg->'workflow_parent';
 IF mineagent_rd_workflow_config(cfg) IS NOT TRUE
 OR q.submission_origin IS DISTINCT FROM 'workflow_stage'
 OR (r.purpose,r.engine,r.idempotency_key) IS DISTINCT FROM ('factor_research'::varchar,'rdagent'::varchar,'workflow-stage:'||st.id::text)
 OR (st.kind,st.child_run_id,st.input_revision_id) IS DISTINCT FROM ('rd_factor_generation'::varchar,r.id,rev.id)
 OR cfg->>'workflow_child_run_id' IS DISTINCT FROM r.id::text
 OR (bind->>'owner_id',bind->>'workflow_id',bind->>'stage_id',bind->>'input_revision_id',bind->>'input_sha256',bind->>'definition_sha256')
 IS DISTINCT FROM (q.owner_id::text,root.id::text,st.id::text,rev.id::text,rev.inputs_sha256,st.definition_sha256)
 OR cfg#>'{workflow_inputs,selection}' IS DISTINCT FROM sel
 OR (sel->>'owner_id',sel->>'workflow_id',sel->>'revision_id') IS DISTINCT FROM (q.owner_id::text,root.id::text,rev.id::text)
 OR mineagent_analysis_raw_commitment(rev.inputs_canonical,sel,rev.inputs_sha256,32768) IS NOT TRUE
 OR mineagent_analysis_raw_commitment(st.definition_canonical,def,st.definition_sha256,32768) IS NOT TRUE
 OR mineagent_analysis_raw_commitment(root.plan_canonical,plan,root.plan_sha256,1048576) IS NOT TRUE
 OR def->>'plan_sha256' IS DISTINCT FROM root.plan_sha256
 OR plan->>'owner_id' IS DISTINCT FROM q.owner_id::text
 OR (q.selection_sha256,q.resolved_inputs_sha256,q.definition_sha256)
 IS DISTINCT FROM (rev.inputs_sha256,cfg->>'workflow_inputs_sha256',st.definition_sha256)
 OR (p.spec_id,p.config_sha256,sp.config_sha256,r.request_sha256,q.config_sha256)
 IS DISTINCT FROM (sp.id,mineagent_execution_metadata_sha(cfg),mineagent_execution_metadata_sha(cfg),mineagent_execution_metadata_sha(cfg),mineagent_execution_metadata_sha(cfg))
 OR sp.config IS DISTINCT FROM cfg
 OR (sp.input_sha256,p.input_sha256) IS DISTINCT FROM (mineagent_execution_metadata_sha(cfg->'input'),mineagent_execution_metadata_sha(cfg->'input'))
 OR mineagent_analysis_raw_commitment(st.allocation_canonical,cfg->'budget',st.allocation_sha256,8192) IS NOT TRUE
 OR mineagent_analysis_raw_commitment(root.budget_canonical,plan->'budget_ceiling',root.budget_sha256,8192) IS NOT TRUE
 OR (cfg-ARRAY['workflow_child_run_id','workflow_inputs','workflow_inputs_sha256','workflow_parent','workflow_prompt_layout'])
 IS DISTINCT FROM ((plan->'research_template')||jsonb_build_object('schema_version','mineagent.factor-research.v3','model_profile','workflow.rdagent.v1'))
 OR (p.objective,p.source_policy) IS DISTINCT FROM (plan->'objective',plan->'source_policy')
 OR (p.label_source_dataset_id,p.label_source_manifest_sha256,p.qlib_image_id,p.qlib_installed_parent_image_id,p.analysis_dependency_lock_sha256)
 IS DISTINCT FROM (r.dataset_version_id,cfg#>>'{input,dataset,content_sha256}',cfg#>>'{runtime,analysis_image}',plan->>'qlib_installed_parent_image_id',plan->>'analysis_dependency_lock_sha256')
 THEN RAISE EXCEPTION 'Workflow child immutable graph differs'; END IF;
 IF NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=r.id AND generation=1)
 OR NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE run_id=r.id AND owner_id=q.owner_id AND limits=cfg->'budget')
 OR EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=r.id)
 OR EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Workflow child requires distinct receipt and durable queued graph'; END IF;
 IF (def->>'research_iteration')::integer=0 THEN
  IF def->'parent_source_stage_id' IS DISTINCT FROM 'null'::jsonb
  OR def->'parent_factor_version_id' IS DISTINCT FROM plan->'initial_parent_factor_version_id'
  THEN RAISE EXCEPTION 'Initial workflow parent differs'; END IF;
 ELSE
  IF (def->>'research_iteration')::integer NOT BETWEEN 1 AND (plan->>'maximum_iterations')::integer-1
  THEN RAISE EXCEPTION 'Finite workflow iteration differs'; END IF;
  SELECT * INTO STRICT prior FROM research_workflow_stages WHERE id=(def->>'parent_source_stage_id')::uuid
   AND owner_id=q.owner_id AND workflow_id=root.id AND ordinal=st.ordinal-2 AND kind='rd_factor_generation';
  SELECT * INTO STRICT fb FROM research_workflow_stages WHERE owner_id=q.owner_id AND workflow_id=root.id
   AND ordinal=st.ordinal-1 AND kind='experiment_feedback_projection';
  SELECT * INTO STRICT fc FROM research_workflow_checkpoints WHERE id=(def->>'predecessor_checkpoint_id')::uuid
   AND owner_id=q.owner_id AND workflow_id=root.id AND stage_id=fb.id;
  SELECT * INTO STRICT rc FROM research_workflow_checkpoints WHERE id=(fc.terminal_canonical::jsonb->>'rd_checkpoint_id')::uuid
   AND owner_id=q.owner_id AND workflow_id=root.id AND stage_id=prior.id;
  IF mineagent_analysis_raw_commitment(fc.terminal_canonical,fc.terminal_canonical::jsonb,fc.terminal_sha256,65536) IS NOT TRUE
  OR mineagent_analysis_raw_commitment(rc.terminal_canonical,rc.terminal_canonical::jsonb,rc.terminal_sha256,65536) IS NOT TRUE
  OR fc.terminal_canonical::jsonb->>'state' IS DISTINCT FROM 'succeeded'
  OR (prior.definition_canonical::jsonb->>'research_iteration')::integer<>(def->>'research_iteration')::integer-1
  OR (rc.terminal_canonical::jsonb->>'child_run_id',rc.terminal_canonical::jsonb->>'factor_version_id',rc.terminal_canonical::jsonb->>'definition_sha256')
   IS DISTINCT FROM (prior.child_run_id::text,def->>'parent_factor_version_id',prior.definition_sha256)
  OR (fc.terminal_canonical::jsonb#>>'{source,workflow_id}',fc.terminal_canonical::jsonb#>>'{source,stage_id}',fc.terminal_canonical::jsonb#>>'{source,child_run_id}',fc.terminal_canonical::jsonb#>>'{source,output_kind}',fc.terminal_canonical::jsonb#>>'{source,output_id}',fc.terminal_canonical::jsonb#>>'{source,content_sha256}')
   IS DISTINCT FROM (root.id::text,prior.id::text,prior.child_run_id::text,'direct_analysis',rc.terminal_canonical::jsonb->>'analysis_id',rc.terminal_canonical::jsonb->>'analysis_result_sha256')
  OR parent->>'code_sha256' IS DISTINCT FROM rc.terminal_canonical::jsonb->>'source_sha256'
  THEN RAISE EXCEPTION 'Workflow prior child and released feedback checkpoint differ'; END IF;
 END IF;
 IF parent='null'::jsonb THEN
  IF def->'parent_factor_version_id' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'Workflow parent missing'; END IF;
 ELSE
  SELECT * INTO STRICT fv FROM factor_versions WHERE id=(parent->>'factor_version_id')::uuid AND owner_id=q.owner_id;
  SELECT * INTO STRICT obj FROM factor_objects WHERE id=fv.code_object_id AND owner_id=q.owner_id;
  IF (parent->>'factor_version_id',parent->>'factor_id',parent->>'ordinal',parent->>'code_object_id',parent->>'code_sha256',parent->>'definition_sha256')
   IS DISTINCT FROM (def->>'parent_factor_version_id',fv.factor_id::text,fv.ordinal::text,fv.code_object_id::text,fv.code_sha256,fv.definition_sha256)
  OR (obj.object_kind,obj.media_type,obj.sha256,obj.size_bytes,obj.storage_key)
   IS DISTINCT FROM ('code'::varchar,'text/plain'::varchar,fv.code_sha256,(parent->>'code_size_bytes')::bigint,'factors/'||q.owner_id::text||'/objects/'||fv.code_sha256||'.py')
  OR octet_length(fv.code)<>obj.size_bytes OR encode(sha256(convert_to(fv.code,'UTF8')),'hex')<>obj.sha256
  OR parent->>'source_provenance_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(fv.source_provenance)
  THEN RAISE EXCEPTION 'Workflow actual parent metadata differs'; END IF;
 END IF;
 SELECT jsonb_agg(value->'name' ORDER BY ord) INTO fields FROM jsonb_array_elements(cfg#>'{input,input_fields}') WITH ORDINALITY f(value,ord);
 expected:=jsonb_build_object('schema_version','mineagent.rdagent-provider-submission.v1','purpose','factor_research','engine','rdagent',
  'dataset_version_id',r.dataset_version_id::text,'window',cfg#>'{input,window}','input_fields',fields,
  'lookback_sessions',cfg#>'{input,lookback_sessions}','lag_sessions',cfg#>'{input,lag_sessions}',
  'task',cfg->'task','objective',p.objective,'budget',cfg->'budget','max_iterations',cfg->'max_iterations',
  'source_policy',p.source_policy,'model_profile_id',cfg#>>'{model_binding,profile_id}','selected_steer_version_ids','[]'::jsonb);
 IF parent<>'null'::jsonb THEN expected:=expected||jsonb_build_object('schema_version','mineagent.rdagent-provider-seeded-submission.v1','parent_factor_version_id',parent->>'factor_version_id'); END IF;
 SELECT '{'||string_agg(mineagent_rd_canonical_json(to_jsonb(key),true)||':'||CASE WHEN key='objective' THEN p.objective_canonical ELSE mineagent_rd_canonical_json(value,true) END,',' ORDER BY key COLLATE "C")||'}' INTO exact_bytes FROM jsonb_each(expected);
 profile_sha:=mineagent_execution_metadata_sha(jsonb_build_object('schema_version','mineagent.rdagent-provider-server-profile.v1','profile','configured_rd_model_single_output_session_v1','model_profile','configured.rdagent.v1','runtime',cfg->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id) || mineagent_rd_controller_selection(cfg));
 preflight:=jsonb_build_object('schema_version','mineagent.rdagent-provider-preflight-binding.v1','owner_id',q.owner_id::text,'request_sha256',q.request_sha256,
  'dataset_content_sha256',p.label_source_manifest_sha256,'frozen_input_sha256',p.input_sha256,'catalog_revision','configured_rd_model_single_output_session_v1',
  'runtime_profile_sha256',profile_sha,'model_binding_sha256',mineagent_execution_metadata_sha(cfg->'model_binding'),
  'steer_snapshot_sha256',cfg->>'steer_snapshot_sha256','parent_binding_sha256',CASE WHEN parent='null'::jsonb THEN NULL ELSE mineagent_execution_metadata_sha(parent) END);
 IF q.request_canonical IS DISTINCT FROM exact_bytes OR q.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(exact_bytes,'UTF8')),'hex')
 OR q.runtime_profile_sha256 IS DISTINCT FROM profile_sha OR q.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(preflight)
 THEN RAISE EXCEPTION 'Workflow request and preflight commitment differ'; END IF;
 IF EXISTS(SELECT 1 FROM factor_generation_starts x WHERE x.run_id=r.id AND x.logical_step=0
  AND (x.owner_id<>q.owner_id OR x.parent_factor_version_id IS DISTINCT FROM (def->>'parent_factor_version_id')::uuid))
 OR (parent<>'null'::jsonb AND EXISTS(SELECT 1 FROM factor_source_publications pub JOIN factor_generation_starts x ON x.id=pub.start_id
  WHERE x.run_id=r.id AND x.logical_step=0 AND pub.factor_id IS DISTINCT FROM (parent->>'factor_id')::uuid))
 THEN RAISE EXCEPTION 'Workflow first source must retain its exact parent family'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_workflow_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Workflow child receipt is immutable'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'Workflow receipt requires READ COMMITTED'; END IF;
 SELECT * INTO STRICT r FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF r.status<>'queued' OR r.generation<>1 OR r.active_attempt_id IS NOT NULL
 OR EXISTS(SELECT 1 FROM execution_attempts WHERE run_id=r.id)
 OR EXISTS(SELECT 1 FROM factor_generation_starts WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Workflow receipt must precede execution authority'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_workflow_submission_append BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.factor_workflow_submissions
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_workflow_append();
CREATE FUNCTION @SCHEMA@.mineagent_rd_workflow_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE sid uuid;
BEGIN
 FOR sid IN SELECT id FROM factor_workflow_submissions ORDER BY run_id LOOP PERFORM mineagent_rd_workflow_submission_check(sid); END LOOP;
 IF EXISTS(SELECT 1 FROM research_runs r WHERE r.config->>'schema_version'='mineagent.factor-research.v3'
  AND NOT EXISTS(SELECT 1 FROM factor_workflow_submissions s WHERE s.run_id=r.id AND s.owner_id=r.owner_id))
 THEN RAISE EXCEPTION 'Workflow child requires its distinct immutable receipt'; END IF;
 RETURN NULL;
END $$;
"""
