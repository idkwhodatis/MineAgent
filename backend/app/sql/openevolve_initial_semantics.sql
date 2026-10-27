-- REVIEW-ONLY fragment. Not loaded by Alembic/startup. Requires the separately
-- reviewed exact model DDL, scoped FKs, unique keys, and append-only triggers.
-- No SECURITY DEFINER, public qualification, model registration, or resume grant.
-- JSON evidence objects remain external: this fragment does NOT recompute native
-- financial metrics or validate object bytes. Those host/object closures remain.

CREATE FUNCTION mineagent_oe_initial_semantics(eid uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE e strategy_search_epochs%ROWTYPE; s strategy_search_specs%ROWTYPE;
        r search_objective_receipts%ROWTYPE; windows text[]; actual text[];
        member_count integer; failed_count integer;
BEGIN
 SELECT * INTO e FROM strategy_search_epochs WHERE id=eid;
 IF NOT FOUND OR e.ordinal IS DISTINCT FROM 1 OR e.reason IS DISTINCT FROM 'initial'
    OR e.first_native_iteration IS DISTINCT FROM 1 THEN
  RAISE EXCEPTION 'OE initial epoch missing or unsupported';
 END IF;
 SELECT * INTO s FROM strategy_search_specs WHERE run_id=e.run_id AND owner_id=e.owner_id;
 IF NOT FOUND OR s.spec_sha256 IS DISTINCT FROM e.spec_sha256 THEN
  RAISE EXCEPTION 'OE epoch spec differs';
 END IF;
 IF jsonb_typeof(s.spec #> '{objective,windows}') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'OE objective windows absent';
 END IF;
 SELECT array_agg(w->>'id' ORDER BY n) INTO windows
 FROM jsonb_array_elements(s.spec #> '{objective,windows}') WITH ORDINALITY AS x(w,n);
 IF cardinality(windows) IS NULL OR cardinality(windows) NOT BETWEEN 1 AND 8
    OR array_position(windows,NULL) IS NOT NULL
    OR (SELECT count(DISTINCT x) FROM unnest(windows) AS x) <> cardinality(windows) THEN
  RAISE EXCEPTION 'OE objective windows invalid';
 END IF;

 -- Cross-table semantic joins supplement, never replace, scoped model FKs.
 FOR r IN SELECT * FROM search_objective_receipts WHERE epoch_id=eid LOOP
  IF (r.owner_id,r.run_id,r.attempt_id,r.generation,r.objective_sha256)
     IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,s.objective_sha256) THEN
   RAISE EXCEPTION 'OE objective scope/profile differs';
  END IF;
  SELECT array_agg(v.window_id ORDER BY v.ordinal),count(*),
         count(*) FILTER (WHERE o.status='failed')
  INTO actual,member_count,failed_count
  FROM search_objective_evaluations v
  LEFT JOIN search_evaluation_outcomes o ON o.id=v.outcome_id
  WHERE v.objective_receipt_id=r.id;
  IF actual IS DISTINCT FROM windows OR member_count <> cardinality(windows) THEN
   RAISE EXCEPTION 'OE objective window membership differs';
  END IF;
  IF EXISTS (
   SELECT 1 FROM search_objective_evaluations v
   LEFT JOIN search_evaluations x ON x.id=v.evaluation_id
   LEFT JOIN search_evaluation_outcomes o ON o.id=v.outcome_id
   WHERE v.objective_receipt_id=r.id AND (
    (v.owner_id,v.run_id,v.attempt_id,v.generation,v.epoch_id,v.candidate_id,v.source_sha256,v.execution_sha256)
      IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,r.candidate_id,r.source_sha256,s.execution_sha256)
    OR v.ordinal IS DISTINCT FROM array_position(windows,v.window_id)-1
    OR (x.owner_id,x.run_id,x.attempt_id,x.generation,x.epoch_id,x.candidate_id,x.source_sha256,x.window_id,x.execution_sha256)
      IS DISTINCT FROM (v.owner_id,v.run_id,v.attempt_id,v.generation,v.epoch_id,v.candidate_id,v.source_sha256,v.window_id,v.execution_sha256)
    OR (o.owner_id,o.run_id,o.attempt_id,o.generation,o.evaluation_id,o.admission_id,o.input_manifest_sha256)
      IS DISTINCT FROM (v.owner_id,v.run_id,v.attempt_id,v.generation,x.id,x.process_admission_id,x.input_manifest_sha256)
    OR o.status IS NULL OR o.status NOT IN ('observed','failed')
   )) THEN RAISE EXCEPTION 'OE objective physical evidence differs'; END IF;
  IF r.status IS NULL OR r.status NOT IN ('eligible','execution_failed','undefined_objective','constraint_failed')
     OR (r.status='execution_failed') IS DISTINCT FROM (failed_count>0)
     OR (r.status='eligible' AND (r.combined_score IS NULL OR r.combined_score IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)))
     OR (r.status<>'eligible' AND r.combined_score IS NOT NULL) THEN
   RAISE EXCEPTION 'OE objective eligibility differs';
  END IF;
 END LOOP;

 IF EXISTS (
  SELECT 1 FROM search_candidate_grants g
  LEFT JOIN search_candidates c ON c.id=g.candidate_id
  LEFT JOIN search_proposal_sources p ON p.id=g.proposal_source_id
  LEFT JOIN search_proposal_responses q ON q.id=p.proposal_id
  LEFT JOIN search_iteration_intents i ON i.id=g.intent_id
  WHERE g.epoch_id=eid AND (
   (g.owner_id,g.run_id,g.attempt_id,g.generation,g.admission_id)
     IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,e.admission_id)
   OR (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.source_sha256,c.kind)
     IS DISTINCT FROM (g.owner_id,g.run_id,g.attempt_id,g.generation,eid,g.source_sha256,g.phase)
   OR (g.phase='seed' AND (g.intent_id IS NOT NULL OR g.proposal_source_id IS NOT NULL
      OR (c.seed_strategy_id,c.source_sha256) IS DISTINCT FROM (s.prepared_strategy_id,s.prepared_source_sha256)))
   OR (g.phase='mutation' AND (
     NOT EXISTS (SELECT 1 FROM search_proposal_sources applied
       WHERE applied.epoch_id=eid AND applied.intent_id=g.intent_id
       AND applied.proposal_id=p.proposal_id AND applied.stage='applied'
       AND (applied.owner_id,applied.run_id,applied.attempt_id,applied.generation)
         IS NOT DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation))
     OR
     (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.intent_id,p.source_sha256,p.stage,p.evidence)
       IS DISTINCT FROM (g.owner_id,g.run_id,g.attempt_id,g.generation,eid,g.intent_id,g.source_sha256,'enforced'::varchar,'complete_source'::varchar)
     OR (q.owner_id,q.run_id,q.attempt_id,q.generation,q.epoch_id,q.intent_id)
       IS DISTINCT FROM (g.owner_id,g.run_id,g.attempt_id,g.generation,eid,g.intent_id)
     OR (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id,i.parent_candidate_id,i.parent_source_sha256)
       IS DISTINCT FROM (g.owner_id,g.run_id,g.attempt_id,g.generation,eid,c.parent_candidate_id,c.parent_source_sha256)))
   OR g.phase IS NULL OR g.phase NOT IN ('seed','mutation')
  )) THEN RAISE EXCEPTION 'OE candidate proposal grant differs'; END IF;

 IF EXISTS (
  SELECT 1 FROM search_worker_outcomes w
  LEFT JOIN search_objective_acknowledgements a ON a.id=w.objective_ack_id
  LEFT JOIN search_objective_receipts o ON o.id=a.objective_receipt_id
  LEFT JOIN search_candidate_grants g ON g.id=a.grant_id
  WHERE w.epoch_id=eid AND (
   (w.owner_id,w.run_id,w.attempt_id,w.generation) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation)
   OR (w.objective_ack_id IS NULL AND w.status IS DISTINCT FROM 'worker_error')
   OR (w.objective_ack_id IS NOT NULL AND (
    (a.owner_id,a.run_id,a.attempt_id,a.generation,a.epoch_id,a.candidate_id,a.source_sha256)
      IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,w.candidate_id,w.source_sha256)
    OR (o.owner_id,o.run_id,o.attempt_id,o.generation,o.epoch_id,o.candidate_id,o.source_sha256)
      IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation,a.epoch_id,a.candidate_id,a.source_sha256)
    OR (g.owner_id,g.run_id,g.attempt_id,g.generation,g.epoch_id,g.intent_id,g.candidate_id,g.source_sha256)
      IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,w.intent_id,w.candidate_id,w.source_sha256)
    OR (w.status='evaluated') IS DISTINCT FROM (o.status='eligible')
   ))
  )) THEN RAISE EXCEPTION 'OE worker objective agreement differs'; END IF;

 IF EXISTS (
  SELECT 1 FROM search_terminal_applications t
  LEFT JOIN search_worker_outcomes w ON w.id=t.worker_outcome_id
  LEFT JOIN search_objective_acknowledgements a ON a.id=COALESCE(t.seed_objective_ack_id,w.objective_ack_id)
  LEFT JOIN search_objective_receipts o ON o.id=a.objective_receipt_id
  WHERE t.epoch_id=eid AND (
   (t.owner_id,t.run_id,t.attempt_id,t.generation) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation)
   OR (t.frontier>0 AND (w.owner_id,w.run_id,w.attempt_id,w.generation,w.epoch_id,w.native_iteration)
     IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,t.frontier))
   OR (t.outcome IN ('seed','accepted','rejected') AND (
     o.status IS DISTINCT FROM 'eligible'
     OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.epoch_id,a.candidate_id,a.source_sha256)
       IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,
          COALESCE(t.seed_candidate_id,w.candidate_id),COALESCE(t.seed_source_sha256,w.source_sha256))
     OR (o.owner_id,o.run_id,o.attempt_id,o.generation,o.epoch_id,o.candidate_id,o.source_sha256)
       IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation,a.epoch_id,a.candidate_id,a.source_sha256)
     OR (t.outcome IN ('accepted','rejected') AND w.status IS DISTINCT FROM 'evaluated')))
   OR (t.outcome='error' AND w.status IS DISTINCT FROM 'worker_error')
  )) THEN RAISE EXCEPTION 'OE terminal eligibility differs'; END IF;

 IF EXISTS (
  SELECT 1 FROM search_control_transitions c
  LEFT JOIN search_controller_event_receipts v ON v.control_id=c.id
  LEFT JOIN search_control_transitions p ON p.id=c.previous_id
  LEFT JOIN search_iteration_intents i ON i.id=c.sampling_intent_id
  WHERE c.epoch_id=eid AND (
   (c.owner_id,c.run_id,c.attempt_id,c.generation) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation)
   OR (v.owner_id,v.run_id,v.attempt_id,v.generation,v.epoch_id,v.admission_id,v.control_sequence,v.control_frontier,v.control_state_sha256,v.command_sha256)
     IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,e.admission_id,c.sequence,c.frontier,c.state_sha256,c.request_sha256)
   OR (c.kind='sampling' AND (v.event_kind,v.intent_id,v.terminal_id) IS DISTINCT FROM ('iteration_intent'::varchar,c.sampling_intent_id,NULL::uuid))
   OR (c.kind='sampling' AND (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id,i.native_iteration)
     IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,v.native_iteration))
   OR (c.kind='sampling' AND (i.native_iteration <= c.frontier
     OR i.native_iteration IS DISTINCT FROM (SELECT count(*)::integer FROM search_control_transitions earlier
        WHERE earlier.epoch_id=eid AND earlier.kind='sampling' AND earlier.sequence<=c.sequence)))
   OR (c.kind='terminal' AND NOT EXISTS (
     SELECT 1 FROM search_terminal_applications terminal
     JOIN search_worker_outcomes worker ON worker.id=terminal.worker_outcome_id
     JOIN search_control_transitions sampled ON sampled.sampling_intent_id=worker.intent_id
     WHERE terminal.id=c.terminal_id AND sampled.epoch_id=eid
       AND sampled.kind='sampling' AND sampled.sequence<c.sequence
   ))
   OR (c.kind IN ('seed','terminal') AND (v.event_kind,v.terminal_id,v.intent_id,v.native_iteration)
     IS DISTINCT FROM ('terminal_applied'::varchar,c.terminal_id,NULL::uuid,c.frontier))
   OR (c.sequence>1 AND (
     (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.sequence)
       IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,c.sequence-1)
     OR (c.kind='sampling' AND c.frontier IS DISTINCT FROM p.frontier)
     OR (c.kind='terminal' AND c.frontier IS DISTINCT FROM p.frontier+1)))
  )) OR EXISTS (
   SELECT 1 FROM search_controller_event_receipts v LEFT JOIN search_control_transitions c ON c.id=v.control_id
   WHERE v.epoch_id=eid AND (c.id IS NULL OR c.epoch_id IS DISTINCT FROM eid)
  ) THEN RAISE EXCEPTION 'OE event control closure differs'; END IF;

 IF EXISTS (
  SELECT 1 FROM search_terminal_applications t
  WHERE t.epoch_id=eid AND NOT EXISTS (
   SELECT 1 FROM search_control_transitions c
   WHERE c.epoch_id=eid AND c.terminal_id=t.id AND c.frontier=t.frontier
     AND c.kind=(CASE WHEN t.frontier=0 THEN 'seed' ELSE 'terminal' END)
  )) OR EXISTS (
  SELECT 1 FROM search_iteration_intents i
  WHERE i.epoch_id=eid AND NOT EXISTS (
   SELECT 1 FROM search_control_transitions c
   WHERE c.epoch_id=eid AND c.sampling_intent_id=i.id AND c.kind='sampling'
  )) THEN RAISE EXCEPTION 'OE domain control coverage differs'; END IF;

 IF EXISTS (
  SELECT 1 FROM search_checkpoint_intents k
  LEFT JOIN search_control_transitions c ON c.id=k.control_transition_id
  LEFT JOIN search_terminal_applications t ON t.id=k.terminal_id
  WHERE k.epoch_id=eid AND (
   (k.owner_id,k.run_id,k.attempt_id,k.generation) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation)
   OR (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.frontier,c.state_sha256)
      IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,k.frontier,k.state_sha256)
   OR (t.owner_id,t.run_id,t.attempt_id,t.generation,t.epoch_id,t.frontier)
      IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,eid,k.frontier)
  )) OR EXISTS (
   SELECT 1 FROM search_checkpoint_publications p LEFT JOIN search_checkpoint_intents k ON k.id=p.intent_id
   WHERE p.epoch_id=eid AND (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.frontier,p.state_sha256)
      IS DISTINCT FROM (k.owner_id,k.run_id,k.attempt_id,k.generation,k.epoch_id,k.frontier,k.state_sha256)
  ) THEN RAISE EXCEPTION 'OE checkpoint scoped closure differs'; END IF;
END $$;

CREATE FUNCTION mineagent_oe_initial_deferred() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE eid uuid;
BEGIN
 IF TG_TABLE_NAME='search_evaluation_outcomes' THEN
  SELECT epoch_id INTO eid FROM search_evaluations WHERE id=NEW.evaluation_id;
 ELSE eid := NEW.epoch_id;
 END IF;
 -- Parity evaluations have no epoch and are outside this initial search slice.
 IF eid IS NOT NULL THEN PERFORM mineagent_oe_initial_semantics(eid); END IF;
 RETURN NEW;
END $$;

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_objective_receipts
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_objective_evaluations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_evaluations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_evaluation_outcomes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_candidate_grants
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_candidates
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_proposal_sources
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_proposal_responses
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_iteration_intents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_objective_acknowledgements
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_worker_outcomes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_terminal_applications
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_control_transitions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_controller_event_receipts
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_checkpoint_intents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();

CREATE CONSTRAINT TRIGGER oe_initial_semantics AFTER INSERT ON search_checkpoint_publications
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_oe_initial_deferred();
