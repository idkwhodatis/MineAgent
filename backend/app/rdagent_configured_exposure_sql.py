"""022 configured admission exposure, distinct from public result release records."""


def render_upgrade_sql():
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_configured_exposure_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  IF OLD.purpose='factor_research_configured_source' THEN RAISE EXCEPTION 'Configured source exposure is immutable'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
 END IF;
 IF NEW.purpose='factor_research_configured_source' THEN
  PERFORM 1 FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM factor_analysis_plans p WHERE p.owner_id=NEW.owner_id AND p.run_id=NEW.run_id AND NEW.source_key='rd-configured-source:'||p.id::text) OR EXISTS(SELECT 1 FROM factor_generation_starts WHERE run_id=NEW.run_id)
  THEN RAISE EXCEPTION 'Configured source exposure must precede generation'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER configured_exposure_append BEFORE INSERT OR UPDATE OR DELETE
ON @SCHEMA@.data_usage_exposures FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_configured_exposure_append();
CREATE FUNCTION @SCHEMA@.mineagent_configured_exposure_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE p record; owner_uuid uuid;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM data_usage_exposures e WHERE e.owner_id=owner_uuid
 AND e.purpose='factor_research_configured_source' AND NOT EXISTS(
 SELECT 1 FROM factor_analysis_plans p WHERE p.owner_id=e.owner_id AND p.run_id=e.run_id
 AND e.source_key='rd-configured-source:'||p.id::text))
 THEN RAISE EXCEPTION 'Configured exposure cannot outlive or invent its plan'; END IF;
 FOR p IN SELECT * FROM factor_analysis_plans WHERE owner_id=owner_uuid LOOP
  IF NOT EXISTS(
   SELECT 1 FROM data_usage_exposures e JOIN dataset_versions d ON d.id=e.dataset_version_id
   WHERE e.owner_id=p.owner_id AND e.run_id=p.run_id AND e.source_key='rd-configured-source:'||p.id::text
    AND e.purpose='factor_research_configured_source' AND d.owner_id=p.owner_id
    AND d.id=p.label_source_dataset_id AND d.content_sha256=p.label_source_manifest_sha256
    AND e.start_date=d.start_date AND e.end_date=d.end_date
    AND e.provenance=jsonb_build_object(
      'schema_version','mineagent.configured-source-exposure.v1','plan_id',p.id::text,
      'config_sha256',p.config_sha256,'input_sha256',p.input_sha256,
      'objective_sha256',p.objective_sha256,'source_policy_sha256',p.source_policy_sha256,
      'dataset_manifest_sha256',p.label_source_manifest_sha256,'objective_window',p.objective->'window',
      'label_horizon_sessions',p.objective->'label_horizon_sessions',
      'extent','entire_retained_dataset_including_history_and_labels',
      'availability','declared_not_independently_verified_pit',
      'interpretation','host_admission_not_proven_candidate_computation')
  ) THEN RAISE EXCEPTION 'Configured plan missing exact source exposure admission'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER configured_plan_exposure_closed AFTER INSERT
ON @SCHEMA@.factor_analysis_plans DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_configured_exposure_consistent();
CREATE CONSTRAINT TRIGGER configured_exposure_plan_closed AFTER INSERT OR UPDATE OR DELETE
ON @SCHEMA@.data_usage_exposures DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_configured_exposure_consistent();
"""


def render_downgrade_sql():
    return r"""
DROP TRIGGER configured_exposure_plan_closed ON @SCHEMA@.data_usage_exposures;
DROP TRIGGER configured_plan_exposure_closed ON @SCHEMA@.factor_analysis_plans;
DROP TRIGGER configured_exposure_append ON @SCHEMA@.data_usage_exposures;
DROP FUNCTION @SCHEMA@.mineagent_configured_exposure_consistent();
DROP FUNCTION @SCHEMA@.mineagent_configured_exposure_append();
"""
