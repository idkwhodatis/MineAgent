"""031 attachment graph: existing records are evidence, never caller authority."""

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_attachment(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE q record; root record; st record; rd_st record; rev record; cp record; sp record; sub record; r record; snap record; exposure record;
 cfg jsonb; ctx jsonb; b jsonb; inputs jsonb; d jsonb; p jsonb; terminal jsonb; expected jsonb; item jsonb; original record; key text; runtime jsonb;
BEGIN
 SELECT * INTO STRICT q FROM oe_workflow_submissions WHERE id=sid;
 SELECT * INTO STRICT root FROM research_workflows WHERE id=q.workflow_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT st FROM research_workflow_stages WHERE id=q.stage_id AND owner_id=q.owner_id AND workflow_id=root.id;
 SELECT * INTO STRICT rev FROM research_workflow_revisions WHERE id=q.input_revision_id AND owner_id=q.owner_id AND workflow_id=root.id;
 SELECT * INTO STRICT rd_st FROM research_workflow_stages WHERE id=q.rd_stage_id AND owner_id=q.owner_id AND workflow_id=root.id;
 SELECT * INTO STRICT cp FROM research_workflow_checkpoints WHERE id=q.rd_checkpoint_id AND owner_id=q.owner_id AND workflow_id=root.id AND stage_id=rd_st.id;
 SELECT * INTO STRICT sp FROM oe_structured_specs WHERE run_id=q.run_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT sub FROM oe_structured_submissions WHERE id=q.submission_id AND run_id=q.run_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT r FROM research_runs WHERE id=q.run_id AND owner_id=q.owner_id;
 SELECT * INTO STRICT snap FROM factor_execution_snapshot_publications WHERE id=q.snapshot_id AND owner_id=q.owner_id AND run_id=q.source_run_id AND attempt_id=q.source_attempt_id AND generation=q.source_generation;
 SELECT * INTO STRICT exposure FROM data_usage_exposures WHERE id=q.release_exposure_id AND owner_id=q.owner_id;
 cfg:=sp.config; ctx:=cfg->'workflow_context'; inputs:=ctx->'inputs'; b:=inputs->'binding'; d:=st.definition_canonical::jsonb; p:=root.plan_canonical::jsonb; terminal:=cp.terminal_canonical::jsonb;
 IF mineagent_workflow_raw(root.plan_canonical,root.plan_sha256,'plan') IS NOT TRUE
 OR p->>'schema_version' IS DISTINCT FROM 'mineagent.rd-structured-guidance-workflow-plan.v1'
 OR mineagent_workflow_raw(st.definition_canonical,st.definition_sha256,'oe_stage') IS NOT TRUE
 OR mineagent_oes_contract('config',cfg) IS NOT TRUE OR cfg->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v2'
 OR mineagent_oes_contract('request',sp.request) IS NOT TRUE OR sp.request->'workflow_context' IS DISTINCT FROM ctx
 OR (q.submission_origin,sub.origin,st.kind,rd_st.kind,r.engine,r.purpose)
   IS DISTINCT FROM ('workflow_stage','workflow_stage','structured_factor_execution','rd_factor_generation','openevolve','strategy_search')
 OR (st.child_run_id,st.input_revision_id,rd_st.child_run_id,sp.submission_id,r.generation,r.idempotency_key)
   IS DISTINCT FROM (q.run_id,q.input_revision_id,q.source_run_id,q.submission_id,1,'workflow-stage:'||st.id::text)
 OR st.ordinal<>2*(p->>'maximum_iterations')::integer+(CASE WHEN (p->>'normalize_external_first')::boolean THEN 1 ELSE 0 END)
 OR rd_st.ordinal<>st.ordinal-1
 OR mineagent_workflow_checkpoint(q.owner_id,q.workflow_id,cp.id) IS NOT TRUE
 OR (q.rd_checkpoint_sha256,q.definition_sha256,q.selection_sha256,q.resolved_inputs_sha256,q.fixed_inputs_sha256)
   IS DISTINCT FROM (cp.terminal_sha256,st.definition_sha256,rev.inputs_sha256,mineagent_execution_metadata_sha(inputs),mineagent_execution_metadata_sha(cfg->'fixed_inputs'))
 OR (q.request_sha256,q.config_sha256,q.preflight_sha256)
   IS DISTINCT FROM (sp.request_sha256,sp.config_sha256,sp.preflight_sha256)
 OR (q.request_sha256,q.config_sha256,q.preflight_sha256)
   IS DISTINCT FROM (sub.request_sha256,sub.config_sha256,sub.preflight_sha256)
 OR (b->>'owner_id',b->>'workflow_id',b->>'stage_id',b->>'input_revision_id',b->>'input_sha256',b->>'definition_sha256')
   IS DISTINCT FROM (q.owner_id::text,root.id::text,st.id::text,rev.id::text,rev.inputs_sha256,st.definition_sha256)
 OR inputs->'selection' IS DISTINCT FROM rev.inputs_canonical::jsonb
 OR mineagent_workflow_raw(rev.inputs_canonical,rev.inputs_sha256,'inputs') IS NOT TRUE
 OR mineagent_workflow_refs(q.owner_id,inputs#>'{selection,refs}') IS NOT TRUE
 OR st.allocation_canonical::jsonb IS DISTINCT FROM p#>'{structured_template,budget}'
 OR st.allocation_canonical::jsonb IS DISTINCT FROM cfg->'budget'
 OR mineagent_execution_metadata_sha(st.allocation_canonical::jsonb) IS DISTINCT FROM st.allocation_sha256
 OR (cfg->'model_binding',cfg->'runtime',cfg#>'{search,objective}',cfg#>'{search,maximum_mutation_slots}')
   IS DISTINCT FROM (p#>'{structured_template,model_binding}',p#>'{structured_template,runtime}',p#>'{structured_template,objective}',p#>'{structured_template,maximum_mutation_slots}')
 OR sp.request->'seed_parameters' IS DISTINCT FROM p#>'{structured_template,seed_parameters}'
 OR (terminal->>'child_run_id',terminal->>'attempt_id',terminal->>'generation',terminal->>'factor_version_id',terminal->>'snapshot_id',terminal->>'analysis_id')
   IS DISTINCT FROM (q.source_run_id::text,q.source_attempt_id::text,q.source_generation::text,q.factor_version_id::text,q.snapshot_id::text,q.analysis_id::text)
 OR (ctx->>'child_run_id',ctx->>'rd_stage_id',ctx->>'rd_checkpoint_id',ctx->>'rd_checkpoint_sha256',ctx->>'source_run_id',ctx->>'source_attempt_id',ctx->>'source_generation',ctx->>'snapshot_id',ctx->>'factor_version_id',ctx->>'analysis_id',ctx->>'release_exposure_id')
   IS DISTINCT FROM (q.run_id::text,q.rd_stage_id::text,q.rd_checkpoint_id::text,cp.terminal_sha256,q.source_run_id::text,q.source_attempt_id::text,q.source_generation::text,q.snapshot_id::text,q.factor_version_id::text,q.analysis_id::text,q.release_exposure_id::text)
 OR (ctx->>'snapshot_manifest_sha256',ctx->>'analysis_result_sha256') IS DISTINCT FROM (terminal->>'snapshot_manifest_sha256',terminal->>'analysis_result_sha256')
 OR snap.factor_version_id IS DISTINCT FROM q.factor_version_id OR snap.manifest_sha256 IS DISTINCT FROM ctx->>'snapshot_manifest_sha256'
 OR cfg#>>'{fixed_inputs,snapshot_manifest_sha256}' IS DISTINCT FROM snap.manifest_sha256
 OR cfg#>>'{fixed_inputs,snapshot_output_sha256}' IS DISTINCT FROM snap.manifest->>'output_rows_sha256'
 OR (exposure.purpose,exposure.source_key,exposure.run_id,exposure.dataset_version_id)
   IS DISTINCT FROM ('engine_execution_snapshot_release','engine-result:execution_snapshot:'||q.snapshot_id::text||':factor:v1',q.source_run_id,sp.dataset_id)
 OR exposure.experiment_id IS NOT NULL OR exposure.split_version_id IS NOT NULL
 OR ctx->>'release_provenance_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(exposure.provenance)
 THEN RAISE EXCEPTION 'Workflow OE immutable predecessor/input/submission graph differs'; END IF;
 expected:=jsonb_build_object('schema_version','mineagent.workflow-oe-stage.v1','plan_sha256',root.plan_sha256,
  'structured_template_sha256',mineagent_execution_metadata_sha(p->'structured_template'),
  'predecessor_checkpoint_id',q.rd_checkpoint_id::text,'predecessor_checkpoint_sha256',cp.terminal_sha256,'parent_source_stage_id',q.rd_stage_id::text,
  'source_run_id',q.source_run_id::text,'source_attempt_id',q.source_attempt_id::text,'source_generation',q.source_generation,
  'factor_version_id',q.factor_version_id::text,'snapshot_id',q.snapshot_id::text,'snapshot_manifest_sha256',snap.manifest_sha256,
  'analysis_id',q.analysis_id::text,'analysis_result_sha256',terminal->>'analysis_result_sha256',
  'release_exposure_id',q.release_exposure_id::text,'release_provenance_sha256',mineagent_execution_metadata_sha(exposure.provenance));
 IF d IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Workflow OE definition differs from actual predecessor'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(inputs->'inputs') LOOP
  IF item#>>'{ref,origin}'='manual_user_submission' THEN
   SELECT * INTO STRICT original FROM rd_steer_versions WHERE owner_id=q.owner_id AND id::text=item#>>'{ref,version_id}' AND steer_id::text=item#>>'{ref,record_id}';
   -- Exact kind/title/text mappings are verified below by the same immutable
   -- version fields the host resolves; display grouping never grants approval.
   IF (item->>'kind',item->>'title',item->>'text') IS DISTINCT FROM (original.kind,'Manual '||original.kind,original.text)
   THEN RAISE EXCEPTION 'Workflow OE manual input bytes differ'; END IF;
  ELSE
   SELECT * INTO STRICT original FROM research_input_versions WHERE owner_id=q.owner_id AND id::text=item#>>'{ref,version_id}' AND record_id::text=item#>>'{ref,record_id}';
   IF (item->>'kind',item->>'title',item->>'text') IS DISTINCT FROM (original.content_canonical::jsonb->>'kind',original.content_canonical::jsonb->>'title',original.content_canonical::jsonb->>'text')
   THEN RAISE EXCEPTION 'Workflow OE derived input bytes differ'; END IF;
  END IF;
  IF item->>'text_sha256' IS DISTINCT FROM encode(sha256(convert_to(item->>'text','UTF8')),'hex')
   THEN RAISE EXCEPTION 'Workflow OE text hash differs'; END IF;
 END LOOP;
 runtime:=jsonb_build_object('schema_version','mineagent.oe-structured-provider-server.v1','runtime',cfg->'runtime');
 expected:=jsonb_build_object('schema_version','mineagent.workflow-oe-preflight.v1','request_sha256',q.request_sha256,
  'fixed_input_sha256',mineagent_execution_metadata_sha(cfg->'fixed_inputs'),'runtime_profile_sha256',mineagent_execution_metadata_sha(runtime),
  'model_binding_sha256',mineagent_execution_metadata_sha(cfg->'model_binding'));
 IF q.runtime_profile_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(runtime)
 OR q.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(expected)
 THEN RAISE EXCEPTION 'Workflow OE exact frozen preflight differs'; END IF;
 IF NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE owner_id=q.owner_id AND run_id=q.run_id AND limits=cfg->'budget')
 OR NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=q.run_id AND generation=1)
 THEN RAISE EXCEPTION 'Workflow OE requires durable original queue and budget'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_before() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Workflow OE attachment is append only'; END IF;
 PERFORM mineagent_workflow_lock(NEW.owner_id,NEW.workflow_id);
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_check() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 PERFORM mineagent_oe_workflow_attachment(NEW.id);
 IF mineagent_workflow_consistent(NEW.owner_id,NEW.workflow_id) IS NOT TRUE THEN RAISE EXCEPTION 'Workflow OE root graph differs'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER oe031_immutable BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.oe_workflow_submissions FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oe_workflow_before();
CREATE CONSTRAINT TRIGGER oe031_graph AFTER INSERT ON @SCHEMA@.oe_workflow_submissions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oe_workflow_check();
"""
