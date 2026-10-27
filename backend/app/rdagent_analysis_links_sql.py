"""022 supplemental declared-analysis finalization;021 actor predicates retained.

SQL binds the actual stored analysis measurement and completed snapshot to the
same run/generation. Feedback body bytes are separately read by host and cold
validators; this predicate does not pretend SQL can inspect opaque object files.
"""


def render_upgrade_sql():
    return r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_analysis_generation_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE g record;
BEGIN
 FOR g IN SELECT outcome.* FROM factor_generation_outcomes outcome
 JOIN factor_analysis_plans plan ON (plan.owner_id,plan.run_id)=(outcome.owner_id,outcome.run_id)
 LOOP
  IF NOT EXISTS (
   SELECT 1 FROM factor_execution_evaluation_outcomes evaluation
   JOIN factor_direct_analysis_intents analysis ON analysis.snapshot_id=evaluation.snapshot_id
   JOIN factor_analysis_plans plan ON plan.id=analysis.plan_id
   JOIN factor_direct_analysis_outcomes measured ON measured.analysis_id=analysis.id
   WHERE (evaluation.owner_id,evaluation.run_id,evaluation.attempt_id,evaluation.generation,evaluation.generation_start_id)=
         (g.owner_id,g.run_id,g.attempt_id,g.generation,g.start_id)
   AND (analysis.owner_id,analysis.run_id,analysis.attempt_id,analysis.generation)=
       (g.owner_id,g.run_id,g.attempt_id,g.generation)
   AND (measured.owner_id,measured.run_id,measured.attempt_id,measured.generation)=
       (g.owner_id,g.run_id,g.attempt_id,g.generation)
   AND (plan.owner_id,plan.run_id)=(g.owner_id,g.run_id)
   AND measured.measurement->>'status'='passed'
   AND measured.measurement->>'reason'='threshold_satisfied'
   AND mineagent_analysis_raw_commitment(measured.measurement_canonical,measured.measurement,measured.measurement_sha256,65536) IS TRUE
   AND g.outcome='accepted' AND evaluation.feedback_object_id=g.feedback_object_id
  ) THEN RAISE EXCEPTION 'Configured adoption requires exact stopped declared analysis passing objective'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER rd_analysis_generation_closed
AFTER INSERT ON @SCHEMA@.factor_generation_outcomes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_rd_analysis_generation_consistent();
CREATE CONSTRAINT TRIGGER rd_analysis_result_generation_closed
AFTER INSERT ON @SCHEMA@.factor_direct_analysis_outcomes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION @SCHEMA@.mineagent_rd_analysis_generation_consistent();
"""


def render_downgrade_sql():
    return r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_analysis_plans)
 THEN RAISE EXCEPTION 'Declared analysis plans cannot downgrade'; END IF;
END $$;
DROP TRIGGER rd_analysis_generation_closed ON @SCHEMA@.factor_generation_outcomes;
DROP TRIGGER rd_analysis_result_generation_closed ON @SCHEMA@.factor_direct_analysis_outcomes;
DROP FUNCTION @SCHEMA@.mineagent_rd_analysis_generation_consistent();
"""
