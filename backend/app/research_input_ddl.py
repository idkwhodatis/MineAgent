"""Source-owned external input guards; literal assembler executes no_parameters.

External normalization and explicitly released direct-development feedback only.
The owning workflow assembler must install its actual source/checkpoint guards. This fragment does not activate a
schema, inspect files, prove process ownership, or replace physical cold closure.
"""

import re

TABLES = (
    "research_input_objects",
    "research_input_derivations",
    "research_input_records",
    "research_input_versions",
    "research_input_derivation_events",
)


def render_sql(schema="public"):
    if re.fullmatch(r"[a-z_][a-z0-9_]*", schema) is None:
        raise ValueError("Closed schema identifier required")
    sql = r"""
CREATE FUNCTION mineagent_input_before() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o uuid; k bigint; w uuid; d uuid;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'retained input rows cannot be deleted'; END IF;
  IF TG_OP='UPDATE' THEN
    IF TG_TABLE_NAME <> 'research_input_records' THEN
      RAISE EXCEPTION 'retained input row is immutable';
    END IF;
    IF NEW.id<>OLD.id OR NEW.owner_id<>OLD.owner_id OR NEW.origin<>OLD.origin OR NEW.created_at<>OLD.created_at OR NEW.revision<>OLD.revision+1 THEN
      RAISE EXCEPTION 'invalid input record transition';
    END IF;
  END IF;
  o:=NEW.owner_id;
  k:=('x'||substr(encode(sha256(convert_to('submit:'||o::text,'UTF8')),'hex'),1,16))::bit(64)::bigint;
  IF NOT pg_try_advisory_xact_lock(k) THEN RAISE EXCEPTION 'input owner is busy'; END IF;
  IF TG_TABLE_NAME IN ('research_input_derivations','research_input_derivation_events') THEN
    w:=NEW.workflow_id;
  ELSIF TG_TABLE_NAME='research_input_versions' THEN
    SELECT workflow_id INTO w FROM research_input_derivations WHERE id=NEW.derivation_id AND owner_id=o;
    IF NOT FOUND THEN RAISE EXCEPTION 'input derivation missing'; END IF;
  ELSIF TG_TABLE_NAME='research_input_records' AND TG_OP='UPDATE' THEN
    SELECT derivation_id INTO d FROM research_input_versions WHERE id=OLD.current_version_id AND owner_id=o AND record_id=OLD.id;
    SELECT workflow_id INTO w FROM research_input_derivations WHERE id=d AND owner_id=o;
    IF NOT FOUND THEN RAISE EXCEPTION 'input record derivation missing'; END IF;
  END IF;
  IF w IS NOT NULL THEN PERFORM mineagent_workflow_lock(o,w); END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION mineagent_input_consistent(o uuid) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE d record; e record; v record; s record; payload jsonb; expected jsonb; previous_state text; previous_time timestamptz; n integer; terminal record;
BEGIN
  IF EXISTS(SELECT 1 FROM research_input_records WHERE owner_id=o AND origin NOT IN ('normalized_external_proposal','experiment_feedback_proposal')) THEN RETURN false; END IF;
  FOR d IN SELECT * FROM research_input_derivations WHERE owner_id=o LOOP
    IF d.operation NOT IN ('external_normalization','experiment_feedback_projection') THEN RETURN false; END IF;
    SELECT * INTO s FROM research_workflow_stages WHERE owner_id=o AND workflow_id=d.workflow_id AND id=d.stage_id;
    IF NOT FOUND OR s.kind<>d.operation OR s.child_run_id IS NOT NULL OR s.definition_canonical<>d.source_canonical OR s.definition_sha256<>d.source_sha256 THEN RETURN false; END IF;
    IF encode(sha256(convert_to(d.source_canonical,'UTF8')),'hex')<>d.source_sha256 OR mineagent_rd_canonical_json(d.source_canonical::jsonb,false)<>d.source_canonical THEN RETURN false; END IF;
    IF d.operation='external_normalization' THEN
    IF encode(sha256(convert_to(d.source_canonical,'UTF8')),'hex')<>d.source_sha256 OR encode(sha256(convert_to(d.mapping_canonical,'UTF8')),'hex')<>d.mapping_sha256 THEN RETURN false; END IF;
    IF mineagent_rd_canonical_json(d.source_canonical::jsonb,false)<>d.source_canonical OR mineagent_rd_canonical_json(d.mapping_canonical::jsonb,false)<>d.mapping_canonical THEN RETURN false; END IF;
    payload:=d.source_canonical::jsonb;
    expected:=jsonb_build_object('schema_version','mineagent.external-normalization-stage.v1','raw_object_id',d.raw_object_id::text,'raw_sha256',d.raw_object_sha256,'mapping',d.mapping_canonical::jsonb);
    IF payload<>expected OR NOT EXISTS(SELECT 1 FROM research_input_objects x WHERE x.owner_id=o AND x.id=d.raw_object_id AND x.sha256=d.raw_object_sha256 AND x.kind='raw_external') THEN RETURN false; END IF;
    ELSE
      payload:=d.source_canonical::jsonb;
      IF d.source_workflow_id<>d.workflow_id OR d.mapping_canonical IS NOT NULL OR d.mapping_sha256 IS NOT NULL OR d.raw_object_id IS NOT NULL OR d.raw_object_sha256 IS NOT NULL THEN RETURN false; END IF;
      IF jsonb_typeof(payload->'source') IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(payload->'source'))<>6 THEN RETURN false; END IF;
      expected:=jsonb_build_object('schema_version','mineagent.feedback-projection-stage.v1','projection_version','mineagent.direct-development-feedback.v1','source',jsonb_build_object('workflow_id',d.source_workflow_id::text,'stage_id',d.source_stage_id::text,'child_run_id',d.source_child_run_id::text,'output_kind','direct_analysis','output_id',payload->'source'->>'output_id','content_sha256',payload->'source'->>'content_sha256'));
      IF payload IS DISTINCT FROM expected THEN RETURN false; END IF;
      IF NOT EXISTS(SELECT 1 FROM research_workflow_stages prior JOIN research_workflow_checkpoints cp ON cp.owner_id=prior.owner_id AND cp.workflow_id=prior.workflow_id AND cp.stage_id=prior.id JOIN factor_direct_analysis_outcomes a ON a.owner_id=prior.owner_id AND a.run_id=prior.child_run_id WHERE prior.owner_id=o AND prior.workflow_id=d.workflow_id AND prior.id=d.source_stage_id AND prior.ordinal=s.ordinal-1 AND prior.kind='rd_factor_generation' AND prior.child_run_id=d.source_child_run_id AND a.analysis_id::text=payload->'source'->>'output_id' AND a.result_sha256=payload->'source'->>'content_sha256' AND cp.terminal_canonical::jsonb->>'analysis_id'=a.analysis_id::text AND cp.terminal_canonical::jsonb->>'analysis_result_sha256'=a.result_sha256 AND cp.terminal_canonical::jsonb->>'child_run_id'=prior.child_run_id::text) THEN RETURN false; END IF;
    END IF;
    n:=0; previous_state:=NULL; previous_time:=NULL;
    FOR e IN SELECT * FROM research_input_derivation_events WHERE derivation_id=d.id ORDER BY ordinal LOOP
      n:=n+1;
      IF e.owner_id<>o OR e.workflow_id<>d.workflow_id OR e.stage_id<>d.stage_id OR e.ordinal<>n OR n>3 OR (previous_time IS NOT NULL AND e.created_at<previous_time) THEN RETURN false; END IF;
      IF (n=1 AND e.state<>'prepared') OR (n=2 AND e.state NOT IN ('started','cancelled')) OR (n=3 AND (previous_state<>'started' OR e.state NOT IN ('succeeded','rejected','failed','cancelled','interrupted'))) THEN RETURN false; END IF;
      IF encode(sha256(convert_to(e.outputs_canonical,'UTF8')),'hex')<>e.outputs_sha256 THEN RETURN false; END IF;
      IF mineagent_rd_canonical_json(e.outputs_canonical::jsonb,false)<>e.outputs_canonical THEN RETURN false; END IF;
      payload:=e.outputs_canonical::jsonb;
      IF jsonb_typeof(payload)<>'array' THEN RETURN false; END IF;
      IF (e.state='succeeded' AND jsonb_array_length(payload) NOT BETWEEN 1 AND 32) OR (e.state<>'succeeded' AND payload<>'[]'::jsonb) THEN RETURN false; END IF;
      IF n=2 AND e.state='cancelled' AND e.error_code IS DISTINCT FROM 'owner_cancelled' THEN RETURN false; END IF;
      IF (e.state IN ('prepared','started','succeeded') AND e.error_code IS NOT NULL) OR (e.state NOT IN ('prepared','started','succeeded') AND e.error_code IS NULL) THEN RETURN false; END IF;
      previous_state:=e.state; previous_time:=e.created_at;
    END LOOP;
    IF n=0 THEN RETURN false; END IF;
    SELECT * INTO terminal FROM research_input_derivation_events WHERE derivation_id=d.id ORDER BY ordinal DESC LIMIT 1;
    IF terminal.state<>'succeeded' AND EXISTS(SELECT 1 FROM research_input_versions WHERE derivation_id=d.id) THEN RETURN false; END IF;
    IF terminal.state='succeeded' THEN
      IF (SELECT count(*) FROM research_input_versions WHERE derivation_id=d.id)<>jsonb_array_length(terminal.outputs_canonical::jsonb) THEN RETURN false; END IF;
      FOR v IN SELECT * FROM research_input_versions WHERE derivation_id=d.id LOOP
        IF v.owner_id<>o OR v.ordinal<>1 OR v.item_ordinal>=jsonb_array_length(terminal.outputs_canonical::jsonb) THEN RETURN false; END IF;
        expected:=jsonb_build_object('origin',(CASE WHEN d.operation='external_normalization' THEN 'normalized_external_proposal' ELSE 'experiment_feedback_proposal' END),'record_id',v.record_id::text,'version_id',v.id::text,'content_sha256',v.content_sha256);
        IF (terminal.outputs_canonical::jsonb->v.item_ordinal)<>expected THEN RETURN false; END IF;
        IF encode(sha256(convert_to(v.content_canonical,'UTF8')),'hex')<>v.content_sha256 THEN RETURN false; END IF;
        IF mineagent_rd_canonical_json(v.content_canonical::jsonb,false)<>v.content_canonical THEN RETURN false; END IF;
        payload:=v.content_canonical::jsonb;
        IF jsonb_typeof(payload)<>'object' THEN RETURN false; END IF;
        IF d.operation='external_normalization' THEN
        IF NOT (payload ?& ARRAY['schema_version','origin','normalizer_version','raw_input_sha256','mapping_sha256','item_pointer','item_ordinal','kind','title','text','trust','validation_scope','instruction_authority','approval_authority']) OR (SELECT count(*) FROM jsonb_object_keys(payload))<>14 THEN RETURN false; END IF;
        IF jsonb_typeof(payload->'title')<>'string' OR jsonb_typeof(payload->'text')<>'string' OR jsonb_typeof(payload->'item_pointer')<>'string' OR jsonb_typeof(payload->'item_ordinal')<>'number' OR length(payload->>'title') NOT BETWEEN 1 AND 200 OR octet_length(payload->>'title')>800 OR length(payload->>'text') NOT BETWEEN 1 AND 2048 OR octet_length(payload->>'text')>8192 OR length(payload->>'item_pointer') NOT BETWEEN 1 AND 2048 OR payload->>'kind' NOT IN ('direction','hypothesis','constraints') OR (payload->>'kind') IS DISTINCT FROM (d.mapping_canonical::jsonb->>'kind') THEN RETURN false; END IF;
        IF COALESCE((payload->>'schema_version'<>'mineagent.normalized-external-proposal.v1' OR payload->>'origin'<>'normalized_external_proposal' OR payload->>'normalizer_version'<>'mineagent.external-json-normalizer.v1' OR payload->>'raw_input_sha256'<>d.raw_object_sha256 OR payload->>'mapping_sha256'<>d.mapping_sha256 OR (payload->>'item_ordinal')::integer<>v.item_ordinal OR payload->>'trust'<>'untrusted_external_data' OR payload->>'validation_scope'<>'format_and_bounds_only' OR payload->>'instruction_authority'<>'none' OR payload->>'approval_authority'<>'none'),true) THEN RETURN false; END IF;
        IF NOT EXISTS(SELECT 1 FROM research_input_objects x WHERE x.owner_id=o AND x.id=v.object_id AND x.kind='normalized_external' AND x.sha256=v.content_sha256 AND x.size_bytes=octet_length(v.content_canonical)) THEN RETURN false; END IF;
        IF NOT EXISTS(SELECT 1 FROM research_input_records r WHERE r.owner_id=o AND r.id=v.record_id AND r.origin='normalized_external_proposal' AND r.current_version_id=v.id) THEN RETURN false; END IF;
        ELSE
          IF v.item_ordinal<>0 OR jsonb_array_length(terminal.outputs_canonical::jsonb)<>1 OR (SELECT count(*) FROM jsonb_object_keys(payload))<>10 OR NOT (payload ?& ARRAY['schema_version','origin','projection_version','source','measurement_sha256','exposure_ids','kind','title','text','qualification']) THEN RETURN false; END IF;
          IF (payload->>'schema_version') IS DISTINCT FROM 'mineagent.experiment-feedback-proposal.v1' OR (payload->>'origin') IS DISTINCT FROM 'experiment_feedback_proposal' OR (payload->>'projection_version') IS DISTINCT FROM 'mineagent.direct-development-feedback.v1' OR (payload->'source') IS DISTINCT FROM (d.source_canonical::jsonb->'source') OR (payload->>'kind') IS DISTINCT FROM 'direction' OR (payload->>'title') IS DISTINCT FROM 'Previous development objective and coverage' OR (payload->>'qualification') IS DISTINCT FROM 'owned_development_measurement_not_out_of_sample_or_adoption_evidence' THEN RETURN false; END IF;
          IF jsonb_typeof(payload->'text') IS DISTINCT FROM 'string' OR length(payload->>'text') NOT BETWEEN 1 AND 2048 OR octet_length(payload->>'text')>8192 OR jsonb_typeof(payload->'exposure_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'exposure_ids')<>2 OR payload->'exposure_ids'->0=payload->'exposure_ids'->1 THEN RETURN false; END IF;
          IF NOT EXISTS(SELECT 1 FROM factor_direct_analysis_outcomes a WHERE a.owner_id=o AND a.run_id=d.source_child_run_id AND a.analysis_id::text=payload->'source'->>'output_id' AND a.result_sha256=payload->'source'->>'content_sha256' AND a.measurement_sha256=payload->>'measurement_sha256') THEN RETURN false; END IF;
          IF (SELECT count(*) FROM data_usage_exposures x WHERE x.owner_id=o AND x.run_id=d.source_child_run_id AND x.purpose='engine_direct_analysis_release' AND x.id::text IN (payload->'exposure_ids'->>0,payload->'exposure_ids'->>1) AND x.source_key IN ('engine-result:direct_analysis:'||(payload->'source'->>'output_id')||':factor:v1','engine-result:direct_analysis:'||(payload->'source'->>'output_id')||':label:v1'))<>2 THEN RETURN false; END IF;
          IF NOT EXISTS(SELECT 1 FROM research_input_objects x WHERE x.owner_id=o AND x.id=v.object_id AND x.kind='normalized_feedback' AND x.sha256=v.content_sha256 AND x.size_bytes=octet_length(v.content_canonical)) THEN RETURN false; END IF;
          IF NOT EXISTS(SELECT 1 FROM research_input_records r WHERE r.owner_id=o AND r.id=v.record_id AND r.origin='experiment_feedback_proposal' AND r.current_version_id=v.id) THEN RETURN false; END IF;
        END IF;
      END LOOP;
    END IF;
  END LOOP;
  IF EXISTS(SELECT 1 FROM research_input_records r WHERE r.owner_id=o AND NOT EXISTS(SELECT 1 FROM research_input_versions sql_version WHERE sql_version.owner_id=o AND sql_version.record_id=r.id AND sql_version.id=r.current_version_id)) THEN RETURN false; END IF;
  RETURN true;
END $$;

CREATE FUNCTION mineagent_input_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT mineagent_input_consistent(NEW.owner_id) THEN RAISE EXCEPTION 'input derivation graph differs'; END IF;
  RETURN NULL;
END $$;
"""
    for table in TABLES:
        sql += f"\nCREATE TRIGGER {table}_before BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_input_before();\n"
        sql += f"CREATE CONSTRAINT TRIGGER {table}_consistent AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_input_check();\n"
    return sql.replace(
        "LANGUAGE plpgsql AS",
        f"LANGUAGE plpgsql SET search_path TO {schema}, pg_catalog AS",
    )
