"""026 literal source: exact seeded receipt and first-parent relational closure.

No old migration bytes are regenerated. The assembler installs these predicates
only with the versioned runtime/backup dispatch, preserving023 fresh semantics.
Actual retained-file reads remain host authority; SQL proves their closed stored
bindings, not that a file was read or a parent was successfully evaluated.
"""


def functions_sql():
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_seed_reference(sid uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=@SCHEMA@,pg_catalog AS $$
 SELECT jsonb_build_object(
  'schema_version','mineagent.rdagent-seed-parent.v1','owner_id',s.owner_id::text,
  'factor_id',s.parent_factor_id::text,'factor_version_id',s.parent_factor_version_id::text,
  'ordinal',s.parent_ordinal,'code_object_id',s.parent_code_object_id::text,
  'code_sha256',s.parent_code_sha256,'code_size_bytes',s.parent_code_size_bytes,
  'definition_sha256',s.parent_definition_sha256,
  'source_provenance_sha256',s.parent_provenance_sha256)
 FROM factor_seeded_submissions s WHERE s.id=sid
$$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_seed_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE s factor_seeded_submissions%ROWTYPE; r research_runs%ROWTYPE;
 p factor_analysis_plans%ROWTYPE; sp factor_research_specs%ROWTYPE;
 v factor_versions%ROWTYPE; o factor_objects%ROWTYPE;
 expected jsonb; exact_bytes text; fields jsonb; profile_sha text; provider boolean; preflight jsonb;
BEGIN
 SELECT * INTO STRICT s FROM factor_seeded_submissions WHERE id=sid;
 SELECT * INTO STRICT r FROM research_runs WHERE id=s.run_id;
 SELECT * INTO STRICT sp FROM factor_research_specs WHERE id=s.spec_id;
 SELECT * INTO STRICT p FROM factor_analysis_plans WHERE id=s.analysis_plan_id;
 provider:=r.config->>'schema_version'='mineagent.factor-research.v2';
 SELECT * INTO STRICT v FROM factor_versions WHERE id=s.parent_factor_version_id;
 SELECT * INTO STRICT o FROM factor_objects WHERE id=s.parent_code_object_id;
 PERFORM mineagent_analysis_plan_check(p.id);
 IF provider THEN PERFORM mineagent_rd_provider_queued_config(r.id); END IF;
 IF (r.owner_id,r.idempotency_key,r.purpose,r.engine,r.request_sha256)
 IS DISTINCT FROM (s.owner_id,s.idempotency_key,'factor_research'::varchar,'rdagent'::varchar,s.config_sha256)
 OR (sp.owner_id,sp.run_id,sp.config_sha256,sp.input_sha256)
 IS DISTINCT FROM (s.owner_id,r.id,s.config_sha256,p.input_sha256)
 OR (p.owner_id,p.run_id,p.spec_id,p.config_sha256)
 IS DISTINCT FROM (s.owner_id,r.id,sp.id,s.config_sha256)
 OR r.config->>'max_iterations' IS DISTINCT FROM '1'
 OR (NOT provider AND (r.config->'task'->>'factor_kind' IS DISTINCT FROM 'time_series'
 OR r.config->'task'->>'output_unit' IS DISTINCT FROM 'return'
 OR r.config->'task'->>'formulation' IS DISTINCT FROM
 ('close[t]/close[t-'||(r.config->'input'->>'lookback_sessions')||']-1')))
 THEN RAISE EXCEPTION 'Seeded request exact run/spec/plan/profile binding differs'; END IF;
 IF (v.owner_id,v.factor_id,v.ordinal,v.code_object_id,v.code_sha256,v.definition_sha256)
 IS DISTINCT FROM (s.owner_id,s.parent_factor_id,s.parent_ordinal,s.parent_code_object_id,s.parent_code_sha256,s.parent_definition_sha256)
 OR (o.owner_id,o.object_kind,o.media_type,o.sha256,o.size_bytes,o.storage_key)
 IS DISTINCT FROM (s.owner_id,'code'::varchar,'text/plain'::varchar,s.parent_code_sha256,
 s.parent_code_size_bytes::bigint,'factors/'||s.owner_id::text||'/objects/'||s.parent_code_sha256||'.py')
 OR octet_length(v.code)<>s.parent_code_size_bytes
 OR encode(sha256(convert_to(v.code,'UTF8')),'hex')<>s.parent_code_sha256
 OR NOT EXISTS(SELECT 1 FROM factors WHERE id=s.parent_factor_id AND owner_id=s.owner_id)
 OR mineagent_analysis_raw_commitment(s.parent_provenance_canonical,v.source_provenance,s.parent_provenance_sha256,65536) IS NOT TRUE
 OR s.parent_binding_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(mineagent_rd_seed_reference(s.id))
 THEN RAISE EXCEPTION 'Seeded parent exact retained metadata binding differs'; END IF;
 IF NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=r.id AND generation=1)
 OR NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE run_id=r.id AND owner_id=r.owner_id AND limits=r.config->'budget')
 OR EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Seeded receipt requires one family, durable outbox and budget'; END IF;
 SELECT jsonb_agg(value->'name' ORDER BY ord) INTO fields
 FROM jsonb_array_elements(r.config->'input'->'input_fields') WITH ORDINALITY f(value,ord);
 IF NOT provider AND (fields @> '["close"]'::jsonb) IS NOT TRUE THEN RAISE EXCEPTION 'Seeded fixed model requires close'; END IF;
 expected:=jsonb_build_object('schema_version','mineagent.rdagent-seeded-submission.v1',
 'purpose','factor_research','engine','rdagent','dataset_version_id',r.dataset_version_id::text,
 'window',r.config->'input'->'window','input_fields',fields,
 'lookback_sessions',r.config->'input'->'lookback_sessions','lag_sessions',r.config->'input'->'lag_sessions',
 'task',r.config->'task','objective',p.objective,'budget',r.config->'budget',
 'max_iterations',r.config->'max_iterations','source_policy',p.source_policy,
 'parent_factor_version_id',s.parent_factor_version_id::text);
 IF provider THEN
  expected:=expected || jsonb_build_object('schema_version','mineagent.rdagent-provider-seeded-submission.v1',
   'model_profile_id',r.config#>>'{model_binding,profile_id}',
   'selected_steer_version_ids',coalesce((SELECT jsonb_agg(value->'version_id' ORDER BY ord)
      FROM jsonb_array_elements(r.config#>'{steer_snapshot,versions}') WITH ORDINALITY x(value,ord)),'[]'::jsonb));
 END IF;
 SELECT '{'||string_agg(mineagent_rd_canonical_json(to_jsonb(key),true)||':'||
 CASE WHEN key='objective' THEN p.objective_canonical ELSE mineagent_rd_canonical_json(value,true) END,
 ',' ORDER BY key COLLATE "C")||'}' INTO exact_bytes FROM jsonb_each(expected);
 profile_sha:=mineagent_execution_metadata_sha(jsonb_build_object(
 'schema_version','mineagent.rdagent-seeded-server-profile.v1','profile','configured_rd_seeded_single_output_session_v1',
 'model_profile','fixture.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id));
 IF provider THEN
  profile_sha:=mineagent_execution_metadata_sha(jsonb_build_object(
   'schema_version','mineagent.rdagent-provider-server-profile.v1','profile','configured_rd_model_single_output_session_v1',
   'model_profile','configured.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id));
  preflight:=jsonb_build_object('schema_version','mineagent.rdagent-provider-preflight-binding.v1',
   'owner_id',s.owner_id::text,'request_sha256',s.request_sha256,
   'dataset_content_sha256',p.label_source_manifest_sha256,'frozen_input_sha256',p.input_sha256,
   'catalog_revision','configured_rd_model_single_output_session_v1','runtime_profile_sha256',profile_sha,
   'model_binding_sha256',mineagent_execution_metadata_sha(r.config->'model_binding'),
   'steer_snapshot_sha256',r.config->>'steer_snapshot_sha256','parent_binding_sha256',s.parent_binding_sha256);
 ELSE
  preflight:=jsonb_build_object(
   'schema_version','mineagent.rdagent-seeded-preflight-binding.v1','owner_id',s.owner_id::text,
   'request_sha256',s.request_sha256,'parent_binding_sha256',s.parent_binding_sha256,
   'dataset_content_sha256',p.label_source_manifest_sha256,'frozen_input_sha256',p.input_sha256,
   'catalog_revision','configured_rd_seeded_single_output_session_v1','runtime_profile_sha256',profile_sha);
 END IF;
 IF s.request_canonical IS DISTINCT FROM exact_bytes
 OR s.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(exact_bytes,'UTF8')),'hex')
 OR s.runtime_profile_sha256 IS DISTINCT FROM profile_sha
 OR s.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(preflight)
 THEN RAISE EXCEPTION 'Seeded canonical declaration or preflight differs'; END IF;
 IF EXISTS(SELECT 1 FROM factor_generation_starts st WHERE st.run_id=r.id AND st.logical_step=0
 AND (st.owner_id<>s.owner_id OR st.parent_factor_version_id IS DISTINCT FROM s.parent_factor_version_id))
 OR EXISTS(SELECT 1 FROM factor_source_publications pub JOIN factor_generation_starts st ON st.id=pub.start_id
 WHERE st.run_id=r.id AND st.logical_step=0 AND pub.factor_id IS DISTINCT FROM s.parent_factor_id)
 THEN RAISE EXCEPTION 'Seeded initial source must retain declared parent and factor family'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_seed_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Seeded receipt is immutable'; END IF;
 IF current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'Seeded receipt requires READ COMMITTED'; END IF;
 SELECT * INTO STRICT r FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF r.status<>'queued' OR r.generation<>1 OR r.active_attempt_id IS NOT NULL
 OR EXISTS(SELECT 1 FROM execution_attempts WHERE run_id=r.id)
 OR EXISTS(SELECT 1 FROM factor_generation_starts WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Seeded declaration must precede all execution authority'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_seed_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE sid uuid;
BEGIN
 FOR sid IN SELECT id FROM factor_seeded_submissions ORDER BY run_id LOOP
  PERFORM mineagent_rd_seed_check(sid);
 END LOOP;
 IF EXISTS(SELECT 1 FROM factor_generation_starts st WHERE st.logical_step=0
 AND st.parent_factor_version_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM factor_seeded_submissions s WHERE s.run_id=st.run_id AND s.owner_id=st.owner_id
 AND s.parent_factor_version_id=st.parent_factor_version_id))
 THEN RAISE EXCEPTION 'A first-generation parent requires its prior immutable seeded receipt'; END IF;
 RETURN NULL;
END $$;
"""
