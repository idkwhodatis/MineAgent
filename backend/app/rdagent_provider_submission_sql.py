"""026 configured declaration binding; no historical function is silently widened."""

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_queued_config(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE; v jsonb; price jsonb; max_cost numeric;
BEGIN
 SELECT * INTO STRICT r FROM research_runs WHERE id=rid;
 v:=r.config; price:=v#>'{model_binding,pricing}';
 IF mineagent_rd_provider_config(v) IS NOT TRUE OR v->>'max_iterations'<>'1'
 OR v#>>'{steer_snapshot,owner_id}'<>r.owner_id::text
 OR price->>'status' IS DISTINCT FROM 'operator_declared'
 THEN RAISE EXCEPTION 'Configured provider declaration differs'; END IF;
 max_cost:=32*ceil(greatest(262144*(price->>'chat_input_microusd_per_million')::numeric
  +(v#>>'{model_binding,max_output_tokens}')::numeric*(price->>'chat_output_microusd_per_million')::numeric,
  262144*(price->>'embedding_microusd_per_million')::numeric)/1000000);
 IF (v#>>'{budget,calls}')::numeric<32
 OR (v#>>'{budget,tokens}')::numeric<32*(262144+(v#>>'{model_binding,max_output_tokens}')::numeric)
 OR (v#>>'{budget,cost_microunits}')::numeric<max_cost
 OR (v#>>'{budget,process_slots}')::numeric<36
 OR (v#>>'{budget,wall_milliseconds}')::numeric<240000+32*(v#>>'{model_binding,request_deadline_ms}')::numeric
 OR (v#>>'{budget,storage_bytes}')::numeric<536870912+32*589824
 OR (v#>>'{budget,evaluator_executions}')::numeric<1
 THEN RAISE EXCEPTION 'Configured provider work exceeds declared admission budget'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_submission_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE s factor_research_submissions%ROWTYPE; r research_runs%ROWTYPE;
 p factor_analysis_plans%ROWTYPE; expected jsonb; exact_bytes text; fields jsonb; profile_sha text;
BEGIN
 SELECT * INTO STRICT s FROM factor_research_submissions WHERE id=sid;
 SELECT * INTO STRICT r FROM research_runs WHERE id=s.run_id;
 SELECT * INTO STRICT p FROM factor_analysis_plans WHERE id=s.plan_id;
 PERFORM mineagent_rd_provider_queued_config(r.id);
 PERFORM mineagent_analysis_plan_check(p.id);
 IF (r.owner_id,r.idempotency_key,r.purpose,r.engine,r.request_sha256)
 IS DISTINCT FROM (s.owner_id,s.idempotency_key,'factor_research'::varchar,'rdagent'::varchar,s.config_sha256)
 OR (p.owner_id,p.run_id,p.config_sha256) IS DISTINCT FROM (s.owner_id,s.run_id,s.config_sha256)
 OR s.catalog_revision<>'configured_rd_model_single_output_session_v1'
 OR EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=r.id)
 OR NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=r.id AND generation=1)
 OR NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE run_id=r.id AND owner_id=r.owner_id AND limits=r.config->'budget')
 THEN RAISE EXCEPTION 'Configured submission exact graph differs'; END IF;
 SELECT jsonb_agg(value->'name' ORDER BY ord) INTO fields
 FROM jsonb_array_elements(r.config#>'{input,input_fields}') WITH ORDINALITY f(value,ord);
 expected:=jsonb_build_object('schema_version','mineagent.rdagent-provider-submission.v1',
 'purpose','factor_research','engine','rdagent','dataset_version_id',r.dataset_version_id::text,
 'window',r.config#>'{input,window}','input_fields',fields,
 'lookback_sessions',r.config#>'{input,lookback_sessions}','lag_sessions',r.config#>'{input,lag_sessions}',
 'task',r.config->'task','objective',p.objective,'budget',r.config->'budget',
 'max_iterations',r.config->'max_iterations','source_policy',p.source_policy,
 'model_profile_id',r.config#>>'{model_binding,profile_id}',
 'selected_steer_version_ids',coalesce((SELECT jsonb_agg(value->'version_id' ORDER BY ord)
  FROM jsonb_array_elements(r.config#>'{steer_snapshot,versions}') WITH ORDINALITY x(value,ord)),'[]'::jsonb));
 SELECT '{'||string_agg(mineagent_rd_canonical_json(to_jsonb(key),true)||':'||
 CASE WHEN key='objective' THEN p.objective_canonical ELSE mineagent_rd_canonical_json(value,true) END,
 ',' ORDER BY key COLLATE "C")||'}' INTO exact_bytes FROM jsonb_each(expected);
 profile_sha:=mineagent_execution_metadata_sha(jsonb_build_object(
 'schema_version','mineagent.rdagent-provider-server-profile.v1','profile','configured_rd_model_single_output_session_v1',
 'model_profile','configured.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id));
 IF s.request_canonical IS DISTINCT FROM exact_bytes
 OR s.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(exact_bytes,'UTF8')),'hex')
 OR s.runtime_profile_sha256 IS DISTINCT FROM profile_sha
 OR s.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object(
 'schema_version','mineagent.rdagent-provider-preflight-binding.v1','owner_id',s.owner_id::text,
 'request_sha256',s.request_sha256,'dataset_content_sha256',p.label_source_manifest_sha256,
 'frozen_input_sha256',p.input_sha256,'catalog_revision',s.catalog_revision,
 'runtime_profile_sha256',profile_sha,'model_binding_sha256',mineagent_execution_metadata_sha(r.config->'model_binding'),
 'steer_snapshot_sha256',r.config->>'steer_snapshot_sha256','parent_binding_sha256',NULL))
 THEN RAISE EXCEPTION 'Configured canonical declaration or preflight differs'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_selection_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE; selection jsonb; head rd_steers%ROWTYPE;
BEGIN
 SELECT * INTO STRICT r FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF r.config->>'schema_version'='mineagent.factor-research.v2' THEN
  PERFORM mineagent_rd_provider_queued_config(r.id);
  -- New acceptance only. Never attach this active/head predicate to historical replay.
  FOR selection IN SELECT value FROM jsonb_array_elements(r.config#>'{steer_snapshot,versions}')
   ORDER BY value->>'steer_id' LOOP
   SELECT * INTO STRICT head FROM rd_steers WHERE id=(selection->>'steer_id')::uuid AND owner_id=r.owner_id FOR SHARE NOWAIT;
   IF head.disabled OR head.current_version_id<>(selection->>'version_id')::uuid
   THEN RAISE EXCEPTION 'New configured selection must be active current version'; END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_provider_selection_fresh BEFORE INSERT ON @SCHEMA@.factor_research_submissions
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_provider_selection_append();
CREATE TRIGGER rd_provider_selection_seed BEFORE INSERT ON @SCHEMA@.factor_seeded_submissions
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_provider_selection_append();
"""


def render_upgrade_sql():
    return SQL


def render_downgrade_sql():
    return """
DROP TRIGGER rd_provider_selection_seed ON @SCHEMA@.factor_seeded_submissions;
DROP TRIGGER rd_provider_selection_fresh ON @SCHEMA@.factor_research_submissions;
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_selection_append();
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_submission_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_queued_config(uuid);
"""
