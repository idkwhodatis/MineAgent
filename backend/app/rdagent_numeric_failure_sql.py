"""027-only failed evaluation delta; relational proof is not physical stop proof."""

TABLES = (
    "factor_numeric_failure_feedbacks",
    "factor_evaluation_outcomes",
    "factor_cutoff_executions",
    "factor_upstream_call_outcomes",
    "research_process_receipts",
    "factor_execution_snapshot_publications",
    "factor_native_publications",
)

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Numeric failure evidence is immutable'; END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_actor() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE actor_uuid uuid;
BEGIN
 IF TG_TABLE_NAME='factor_numeric_failure_feedbacks' THEN actor_uuid:=NEW.controller_launch_id; END IF;
 IF TG_TABLE_NAME='factor_evaluation_outcomes' THEN
  IF NEW.status<>'execution_failed' THEN RAISE EXCEPTION 'Only observed numeric failure is admitted'; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM research_runs r JOIN execution_attempts a ON a.id=NEW.attempt_id
 JOIN factor_research_specs spec ON spec.run_id=r.id
 WHERE (r.id,r.owner_id,r.active_attempt_id,r.generation)=(NEW.run_id,NEW.owner_id,NEW.attempt_id,NEW.generation)
 AND (a.run_id,a.generation)=(r.id,r.generation) AND a.lease_expires_at>clock_timestamp()
 AND r.status='running' AND a.status='running'
 AND spec.config->>'model_profile'='configured.rdagent.v1'
 AND EXISTS(SELECT 1 FROM factor_controller_launches cl
 JOIN research_process_admissions p ON p.id=cl.admission_id
 JOIN research_process_states st ON st.admission_id=p.id
 WHERE (cl.owner_id,cl.run_id,cl.attempt_id,cl.generation)=(NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation)
 AND (actor_uuid IS NULL OR cl.id=actor_uuid)
 AND p.profile IN ('mineagent.rdagent-orchestrator.v2','mineagent.rdagent-orchestrator.v3')
 AND mineagent_rd_controller_profile_matches(spec.config,p.profile) IS TRUE AND st.status='running'
 AND EXISTS(SELECT 1 FROM research_process_receipts cr WHERE cr.admission_id=p.id AND cr.kind='controller_child')))
 THEN RAISE EXCEPTION 'Numeric failure requires current configured controller'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_graph() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE owner_uuid uuid;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM factor_numeric_failure_feedbacks f
 LEFT JOIN factor_evaluation_outcomes result ON result.id=f.evaluation_outcome_id
 LEFT JOIN factor_evaluation_starts ev ON ev.id=f.evaluation_id
 LEFT JOIN factor_generation_sources src ON src.id=ev.evaluated_source_id
 LEFT JOIN factor_cutoff_executions cut ON cut.id=f.cutoff_execution_id
 LEFT JOIN factor_controller_launches cl ON cl.id=f.controller_launch_id
 LEFT JOIN factor_upstream_calls call ON call.id=f.final_decision_call_id
 LEFT JOIN factor_upstream_call_outcomes done ON done.call_id=call.id
 LEFT JOIN research_process_receipts rejected ON rejected.id=f.rejection_receipt_id
 LEFT JOIN research_process_receipts stopped ON stopped.id=f.stop_receipt_id
 LEFT JOIN research_objects obj ON obj.id=f.feedback_object_id
 WHERE f.owner_id=owner_uuid AND (
 result.id IS NULL OR ev.id IS NULL OR src.id IS NULL OR cut.id IS NULL OR cl.id IS NULL
 OR call.id IS NULL OR done.id IS NULL OR rejected.id IS NULL OR stopped.id IS NULL OR obj.id IS NULL
 OR (result.owner_id,result.run_id,result.attempt_id,result.generation,result.evaluation_id,result.status,
 result.feedback_object_id,result.feedback_sha256) IS DISTINCT FROM
 (f.owner_id,f.run_id,f.attempt_id,f.generation,f.evaluation_id,'execution_failed'::varchar,f.feedback_object_id,f.feedback_sha256)
 OR result.snapshot_id IS NOT NULL OR result.snapshot_input_sha256 IS NOT NULL OR result.snapshot_output_sha256 IS NOT NULL
 OR ev.expected_cutoffs<>1 OR (SELECT count(*) FROM factor_cutoff_executions WHERE evaluation_id=ev.id)<>1
 OR (cut.owner_id,cut.run_id,cut.attempt_id,cut.generation,cut.evaluation_id,cut.evaluated_source_id,
 cut.start_id,cut.factor_version_id,cut.cutoff_ordinal,cut.candidate_admission_id) IS DISTINCT FROM
 (f.owner_id,f.run_id,f.attempt_id,f.generation,ev.id,src.id,src.start_id,src.factor_version_id,0,f.candidate_admission_id)
 OR (call.owner_id,call.run_id,call.attempt_id,call.generation,call.start_id,call.phase,call.kind) IS DISTINCT FROM
 (f.owner_id,f.run_id,f.attempt_id,f.generation,src.start_id,'final_decision'::varchar,'chat'::varchar)
 OR done.controller_launch_id<>cl.id OR done.status<>'completed' OR done.member_count<>1
 OR (cl.owner_id,cl.run_id,cl.attempt_id,cl.generation) IS DISTINCT FROM (f.owner_id,f.run_id,f.attempt_id,f.generation)
 OR (rejected.admission_id,rejected.kind,rejected.payload_sha256) IS DISTINCT FROM
 (f.candidate_admission_id,'rejection'::varchar,f.rejection_sha256)
 OR (stopped.admission_id,stopped.kind,stopped.payload_sha256) IS DISTINCT FROM
 (f.candidate_admission_id,'stop'::varchar,f.stop_sha256)
 OR (obj.owner_id,obj.object_kind,obj.sha256) IS DISTINCT FROM
 (f.owner_id,'factor_generation_feedback'::varchar,f.feedback_sha256)
 OR obj.size_bytes>65536
 OR mineagent_numeric_rejection_stopped(f.candidate_admission_id) IS NOT TRUE
 OR EXISTS(SELECT 1 FROM factor_execution_snapshot_publications WHERE evaluation_id=ev.id)
 OR EXISTS(SELECT 1 FROM factor_native_publications WHERE admission_id=f.candidate_admission_id)
 OR EXISTS(SELECT 1 FROM factor_generation_outcomes WHERE start_id=src.start_id)
 )) THEN RAISE EXCEPTION 'Failed evaluation lacks exact stopped rejection and no-snapshot feedback closure'; END IF;
 IF EXISTS(SELECT 1 FROM factor_evaluation_outcomes ev WHERE ev.owner_id=owner_uuid
 AND NOT EXISTS(SELECT 1 FROM factor_numeric_failure_feedbacks f WHERE f.evaluation_outcome_id=ev.id))
 THEN RAISE EXCEPTION 'Legacy evaluation result requires exact observed failure consumer'; END IF;
 RETURN NULL;
END $$;
"""


def render_upgrade_sql():
    sql = (
        SQL
        + """
DROP TRIGGER rd_source_phase_closed ON @SCHEMA@.factor_evaluation_outcomes;
CREATE TRIGGER rd_numeric_failure_actor BEFORE INSERT ON @SCHEMA@.factor_evaluation_outcomes
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_actor();
CREATE TRIGGER rd_numeric_failure_actor BEFORE INSERT ON @SCHEMA@.factor_numeric_failure_feedbacks
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_actor();
CREATE TRIGGER rd_numeric_failure_lock BEFORE INSERT ON @SCHEMA@.factor_numeric_failure_feedbacks
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_source_run_lock();
CREATE TRIGGER rd_numeric_failure_immutable BEFORE UPDATE OR DELETE ON @SCHEMA@.factor_numeric_failure_feedbacks
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_immutable();
"""
    )
    for table in TABLES:
        sql += f"\nCREATE CONSTRAINT TRIGGER rd_numeric_failure_graph AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_graph();"
    return sql


def render_downgrade_sql():
    sql = """DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_numeric_failure_feedbacks)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.factor_evaluation_outcomes)
 THEN RAISE EXCEPTION 'Numeric failure evidence prevents downgrade'; END IF; END $$;"""
    for table in TABLES:
        sql += f"\nDROP TRIGGER rd_numeric_failure_graph ON @SCHEMA@.{table};"
    for table in ("factor_evaluation_outcomes", "factor_numeric_failure_feedbacks"):
        sql += f"\nDROP TRIGGER rd_numeric_failure_actor ON @SCHEMA@.{table};"
    return (
        sql
        + """
DROP TRIGGER rd_numeric_failure_lock ON @SCHEMA@.factor_numeric_failure_feedbacks;
DROP TRIGGER rd_numeric_failure_immutable ON @SCHEMA@.factor_numeric_failure_feedbacks;
CREATE TRIGGER rd_source_phase_closed BEFORE INSERT ON @SCHEMA@.factor_evaluation_outcomes
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_source_only_phase();
DROP FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_graph();
DROP FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_actor();
DROP FUNCTION @SCHEMA@.mineagent_rd_numeric_failure_immutable();
"""
    )
