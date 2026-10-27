"""022-only relational witness closure, not proof of filesystem reads.

The migration assembler installs the mapped table first. Host readers independently
reread retained source/marketdata bytes; SQL cannot prove those physical reads.
Historical source and process predicates remain installed unchanged.
"""


def render_upgrade_sql():
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_configured_input_append() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'configured input is immutable'; END IF;
 PERFORM 1 FROM @SCHEMA@.research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'configured input run scope'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_configured_input_consistent() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE w record;
BEGIN
 IF EXISTS (
 SELECT 1 FROM @SCHEMA@.factor_cutoff_executions c
 JOIN @SCHEMA@.factor_analysis_plans p ON (p.owner_id,p.run_id)=(c.owner_id,c.run_id)
 LEFT JOIN @SCHEMA@.factor_configured_input_witnesses cutoff_witness ON cutoff_witness.execution_id=c.id
 WHERE cutoff_witness.id IS NULL
 ) THEN RAISE EXCEPTION 'configured cutoff missing input witness'; END IF;
 FOR w IN SELECT * FROM @SCHEMA@.factor_configured_input_witnesses LOOP
  IF NOT EXISTS (
   SELECT 1 FROM @SCHEMA@.factor_cutoff_executions c
   JOIN @SCHEMA@.factor_evaluation_starts e ON e.id=c.evaluation_id
   JOIN @SCHEMA@.factor_generation_sources src ON src.id=c.evaluated_source_id
   JOIN @SCHEMA@.factor_source_read_witnesses r ON r.id=w.source_read_witness_id
   JOIN @SCHEMA@.factor_analysis_plans p ON p.id=w.plan_id
   JOIN @SCHEMA@.factor_research_specs s ON s.id=p.spec_id
   JOIN @SCHEMA@.dataset_versions d ON d.id=w.dataset_id
   JOIN @SCHEMA@.research_process_admissions a ON a.id=w.admission_id
   WHERE c.id=w.execution_id
    AND (c.owner_id,c.run_id,c.attempt_id,c.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
    AND (e.owner_id,e.run_id,e.attempt_id,e.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
    AND (src.owner_id,src.run_id,src.attempt_id,src.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
    AND (r.owner_id,r.run_id,r.attempt_id,r.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
    AND (a.owner_id,a.run_id,a.attempt_id,a.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
    AND p.owner_id=w.owner_id AND p.run_id=w.run_id AND d.owner_id=w.owner_id
    AND c.candidate_admission_id=a.id AND a.logical_operation_id=c.id
    AND a.schema_version='mineagent.process-admission.v2'
    AND a.role='factor_cutoff_candidate' AND a.ipc_policy='none' AND a.ticket_sha256 IS NULL
    AND a.launch_ordinal=1 AND a.profile=c.projection->>'candidate_profile'
    AND a.image_id=c.projection->>'candidate_image' AND a.input_sha256=w.input_sha256
    AND c.projection_sha256=w.projection_sha256
    AND jsonb_typeof(w.document->'files')='array' AND jsonb_array_length(w.document->'files')=4
    AND (SELECT array_agg(value->>'destination' ORDER BY ord) FROM jsonb_array_elements(w.document->'files') WITH ORDINALITY x(value,ord))=ARRAY['/input/listings.json','/input/manifest.json','/input/observations.json','/input/source.py']
    AND encode(sha256(convert_to(@SCHEMA@.mineagent_rd_ascii_metadata(jsonb_build_object(
      'schema_version','mineagent.numeric-process-input.v2','source_object_id',r.source_object_id::text,
      'source_sha256',r.source_sha256,'prefix_sha256',w.projection_sha256,
      'cutoff',c.projection->'host_cutoff','files',w.document->'files')),'UTF8')),'hex')=w.input_sha256
    AND r.source_id=src.id AND r.source_sha256=src.source_sha256
    AND e.evaluated_source_id=src.id AND e.start_id=c.start_id AND src.start_id=c.start_id
    AND w.document->>'source_read_witness_sha256'=r.witness_sha256
    AND encode(sha256(convert_to(@SCHEMA@.mineagent_rd_ascii_metadata(w.document),'UTF8')),'hex')=w.witness_sha256
    AND d.content_sha256=w.dataset_manifest_sha256
    AND p.label_source_dataset_id=d.id AND p.label_source_manifest_sha256=d.content_sha256
    AND s.config_sha256=p.config_sha256 AND s.input_sha256=p.input_sha256
    AND s.config->'input'->'dataset'->>'dataset_version_id'=d.id::text
    AND s.config->'input'->'dataset'->>'availability_revision_sha256'=w.availability_revision_sha256
    AND s.config->'input'->>'calendar_sha256'=w.calendar_sha256
    AND w.document->>'schema_version'='mineagent.configured-factor-input-witness.v1'
    AND w.document->>'owner_id'=w.owner_id::text AND w.document->>'run_id'=w.run_id::text
    AND w.document->>'attempt_id'=w.attempt_id::text AND w.document->>'generation'=w.generation::text
    AND w.document->>'execution_id'=c.id::text AND w.document->>'admission_id'=a.id::text
    AND w.document->>'evaluation_id'=e.id::text AND w.document->>'source_id'=src.id::text
    AND w.document->>'plan_id'=p.id::text AND w.document->>'dataset_id'=d.id::text
    AND w.document->>'source_read_witness_id'=r.id::text
    AND w.document->>'dataset_manifest_sha256'=w.dataset_manifest_sha256
    AND w.document->>'availability_revision_sha256'=w.availability_revision_sha256
    AND w.document->>'calendar_sha256'=w.calendar_sha256
    AND w.document->>'input_sha256'=w.input_sha256
    AND w.document->>'projection_sha256'=w.projection_sha256
  ) THEN RAISE EXCEPTION 'configured input witness binding'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER configured_input_consistent
AFTER INSERT ON @SCHEMA@.factor_configured_input_witnesses
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_configured_input_consistent();
CREATE CONSTRAINT TRIGGER configured_cutoff_input_consistent
AFTER INSERT ON @SCHEMA@.factor_cutoff_executions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_configured_input_consistent();
CREATE TRIGGER configured_input_immutable BEFORE INSERT OR UPDATE OR DELETE
ON @SCHEMA@.factor_configured_input_witnesses FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_configured_input_append();
"""


def render_downgrade_sql():
    return r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_configured_input_witnesses)
 THEN RAISE EXCEPTION 'configured input evidence cannot downgrade'; END IF;
END $$;
DROP TRIGGER configured_cutoff_input_consistent ON @SCHEMA@.factor_cutoff_executions;
DROP TRIGGER configured_input_immutable ON @SCHEMA@.factor_configured_input_witnesses;
DROP TRIGGER configured_input_consistent ON @SCHEMA@.factor_configured_input_witnesses;
DROP FUNCTION @SCHEMA@.mineagent_configured_input_consistent();
DROP FUNCTION @SCHEMA@.mineagent_configured_input_append();
"""
