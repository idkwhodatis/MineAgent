"""023 receipt semantics. Migration must freeze literals; no launch authority.

The objective's original canonical bytes come from the 022 immutable plan. Never
reconstruct floating-point spelling from JSONB. Remaining request fields have
closed string/boolean/integer grammars and use the historical canonical helper.
"""


def render_upgrade_sql():
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_submission_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE s factor_research_submissions%ROWTYPE; r research_runs%ROWTYPE;
 p factor_analysis_plans%ROWTYPE; expected jsonb; exact_bytes text; input_fields jsonb;
BEGIN
 SELECT * INTO STRICT s FROM factor_research_submissions WHERE id=sid;
 SELECT * INTO STRICT r FROM research_runs WHERE id=s.run_id;
 SELECT * INTO STRICT p FROM factor_analysis_plans WHERE id=s.plan_id;
 PERFORM mineagent_analysis_plan_check(p.id);
 IF (r.owner_id,r.idempotency_key,r.purpose,r.engine,r.request_sha256)
 IS DISTINCT FROM (s.owner_id,s.idempotency_key,'factor_research'::varchar,'rdagent'::varchar,s.config_sha256)
 OR (p.owner_id,p.run_id,p.config_sha256) IS DISTINCT FROM (s.owner_id,s.run_id,s.config_sha256)
 THEN RAISE EXCEPTION 'Submission exact owner/run/plan binding differs'; END IF;
 IF NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=r.id AND generation=1)
 OR NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE run_id=r.id AND owner_id=r.owner_id AND limits=r.config->'budget')
 THEN RAISE EXCEPTION 'Submission durable outbox or immutable budget missing'; END IF;
 SELECT jsonb_agg(value->'name' ORDER BY ord) INTO input_fields
 FROM jsonb_array_elements(r.config->'input'->'input_fields') WITH ORDINALITY f(value,ord);
 expected:=jsonb_build_object('schema_version','mineagent.rdagent-submission.v1',
 'purpose','factor_research','engine','rdagent','dataset_version_id',r.dataset_version_id::text,
 'window',r.config->'input'->'window','input_fields',input_fields,
 'lookback_sessions',r.config->'input'->'lookback_sessions',
 'lag_sessions',r.config->'input'->'lag_sessions','task',r.config->'task',
 'objective',p.objective,'budget',r.config->'budget','max_iterations',r.config->'max_iterations',
 'source_policy',p.source_policy);
 SELECT '{'||string_agg(mineagent_rd_canonical_json(to_jsonb(key),true)||':'||
 CASE WHEN key='objective' THEN p.objective_canonical ELSE mineagent_rd_canonical_json(value,true) END,
 ',' ORDER BY key COLLATE "C")||'}' INTO exact_bytes FROM jsonb_each(expected);
 IF s.request_canonical IS DISTINCT FROM exact_bytes
 OR s.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(exact_bytes,'UTF8')),'hex')
 OR s.runtime_profile_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object(
 'schema_version','mineagent.rdagent-server-profile.v1','profile','configured_rd_single_output_session_v1',
 'model_profile','fixture.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id))
 OR s.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object(
 'schema_version','mineagent.rdagent-preflight-binding.v1','owner_id',s.owner_id::text,
 'request_sha256',s.request_sha256,'dataset_content_sha256',p.label_source_manifest_sha256,
 'frozen_input_sha256',p.input_sha256,'catalog_revision',s.catalog_revision,
 'runtime_profile_sha256',s.runtime_profile_sha256))
 THEN RAISE EXCEPTION 'Submission canonical declaration or preflight differs'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_submission_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Submission receipt is immutable'; END IF;
 IF current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'Submission requires READ COMMITTED'; END IF;
 SELECT * INTO STRICT r FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF r.status<>'queued' OR r.generation<>1 OR r.active_attempt_id IS NOT NULL
 OR EXISTS(SELECT 1 FROM execution_attempts WHERE run_id=r.id)
 OR EXISTS(SELECT 1 FROM factor_generation_starts WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Initial submission must be queued without execution authority'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_submission_append BEFORE INSERT OR UPDATE OR DELETE
ON @SCHEMA@.factor_research_submissions FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_append();
CREATE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE sid uuid; rid uuid;
BEGIN
 IF TG_TABLE_NAME='factor_research_submissions' THEN
  PERFORM mineagent_rd_submission_check(NEW.id);
 ELSE
  IF TG_TABLE_NAME='research_runs' THEN rid:=NEW.id;
  ELSIF TG_OP='DELETE' THEN rid:=OLD.run_id;
  ELSE rid:=NEW.run_id; END IF;
  FOR sid IN SELECT id FROM factor_research_submissions WHERE run_id=rid LOOP
   PERFORM mineagent_rd_submission_check(sid);
  END LOOP;
  IF TG_TABLE_NAME='research_runs' THEN
  IF TG_OP='INSERT' AND NEW.purpose='factor_research' AND NEW.engine='rdagent' AND NEW.status='queued'
  AND NOT EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id)
  THEN RAISE EXCEPTION 'Queued RD submission requires immutable request receipt'; END IF;
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER rd_submission_closed AFTER INSERT ON @SCHEMA@.factor_research_submissions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();
CREATE CONSTRAINT TRIGGER rd_submitted_run_closed AFTER INSERT OR UPDATE ON @SCHEMA@.research_runs
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();
CREATE CONSTRAINT TRIGGER rd_submission_outbox_closed AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.outbox_events
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();
CREATE CONSTRAINT TRIGGER rd_submission_budget_closed AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.research_budget_controls
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();
"""


def render_downgrade_sql():
    return r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_research_submissions)
 THEN RAISE EXCEPTION 'Submission receipts cannot be represented before023'; END IF;
END $$;
DROP TRIGGER rd_submission_budget_closed ON @SCHEMA@.research_budget_controls;
DROP TRIGGER rd_submission_outbox_closed ON @SCHEMA@.outbox_events;
DROP TRIGGER rd_submitted_run_closed ON @SCHEMA@.research_runs;
DROP TRIGGER rd_submission_closed ON @SCHEMA@.factor_research_submissions;
DROP TRIGGER rd_submission_append ON @SCHEMA@.factor_research_submissions;
DROP FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();
DROP FUNCTION @SCHEMA@.mineagent_rd_submission_append();
DROP FUNCTION @SCHEMA@.mineagent_rd_submission_check(uuid);
"""
