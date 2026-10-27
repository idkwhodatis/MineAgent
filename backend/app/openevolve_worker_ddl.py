"""024 immutable worker metadata closure, not kernel or file observation.

Retained identity/outcome files contain placement/event SHA and native child
observations that these tables do not inline. Physical cold/live readers must
still validate those bytes. This SQL checks only independently stored relations.
Sampling may commit before placement; missing downstream rows are not success.
"""

import re

TABLES = (
    "search_worker_tickets",
    "search_worker_expected_identities",
    "search_worker_assignments",
    "search_worker_assignment_closures",
    "search_iteration_intents",
    "search_controller_event_receipts",
    "search_control_transitions",
    "strategy_search_epochs",
    "research_process_admissions",
    "research_process_receipts",
    "research_objects",
    "search_worker_outcomes",
    "search_candidates",
    "search_candidate_grants",
    "search_objective_acknowledgements",
    "search_objective_receipts",
)


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact SQL schema required")
    return (
        SQL.replace("@S@", schema)
        + "\n"
        + "\n".join(
            f"CREATE CONSTRAINT TRIGGER oe024_worker_consistent AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_oe_worker_consistent();"
            for table in TABLES
        )
    )


SQL = r"""
CREATE FUNCTION @S@.mineagent_oe_worker_valid(iid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE i record; e record; a record; t record; event_row record; control_row record;
 x record; assigned record; closed record; outcome_row record; object_row record;
 child jsonb; ack record; grant_row record; candidate record; objective record;
BEGIN
 SELECT * INTO i FROM search_iteration_intents WHERE id=iid;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO e FROM strategy_search_epochs WHERE id=i.epoch_id;
 IF NOT FOUND OR (e.owner_id,e.run_id,e.attempt_id,e.generation)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation) THEN RETURN false; END IF;
 SELECT * INTO a FROM research_process_admissions WHERE id=e.admission_id;
 IF NOT FOUND OR (a.owner_id,a.run_id,a.attempt_id,a.generation)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation)
 OR a.profile<>'mineagent.openevolve-orchestrator.v1' OR a.role<>'openevolve_orchestrator'
 OR a.ipc_policy<>'host_ticket' THEN RETURN false; END IF;
 SELECT * INTO t FROM search_worker_tickets WHERE intent_id=i.id;
 IF NOT FOUND OR (t.owner_id,t.run_id,t.attempt_id,t.generation,t.epoch_id,t.admission_id)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,a.id)
 OR t.ticket_sha256 !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
 SELECT * INTO event_row FROM search_controller_event_receipts WHERE id=t.controller_receipt_id;
 IF NOT FOUND OR (event_row.owner_id,event_row.run_id,event_row.attempt_id,event_row.generation,event_row.epoch_id,event_row.admission_id,event_row.intent_id,event_row.native_iteration)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,a.id,i.id,i.native_iteration)
 OR event_row.event_kind<>'iteration_intent' OR event_row.terminal_id IS NOT NULL THEN RETURN false; END IF;
 SELECT * INTO control_row FROM search_control_transitions WHERE id=event_row.control_id;
 IF NOT FOUND OR (control_row.owner_id,control_row.run_id,control_row.attempt_id,control_row.generation,control_row.epoch_id,control_row.sequence,control_row.frontier,control_row.state_sha256,control_row.request_sha256,control_row.sampling_intent_id)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,event_row.control_sequence,event_row.control_frontier,event_row.control_state_sha256,event_row.command_sha256,i.id)
 OR control_row.kind<>'sampling' OR control_row.terminal_id IS NOT NULL THEN RETURN false; END IF;
 SELECT * INTO object_row FROM research_objects WHERE id=event_row.event_object_id;
 IF NOT FOUND OR (object_row.owner_id,object_row.object_kind,object_row.sha256)
 IS DISTINCT FROM (i.owner_id,'search_controller_event'::varchar,event_row.event_sha256)
 OR event_row.event_object_kind<>'search_controller_event' THEN RETURN false; END IF;
 SELECT * INTO x FROM search_worker_expected_identities WHERE intent_id=i.id;
 IF NOT FOUND THEN
  -- A committed sampling/ticket can precede the genuine pool-placement event.
  RETURN NOT EXISTS(SELECT 1 FROM search_worker_assignments WHERE intent_id=i.id)
   AND NOT EXISTS(SELECT 1 FROM search_worker_outcomes WHERE intent_id=i.id);
 END IF;
 IF (x.owner_id,x.run_id,x.attempt_id,x.generation,x.epoch_id,x.admission_id,x.ticket_id)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,a.id,t.id)
 OR x.host_pid<=0 OR x.start_ticks<=0 THEN RETURN false; END IF;
 SELECT payload INTO child FROM research_process_receipts
 WHERE admission_id=a.id AND kind='controller_child'
 AND (owner_id,run_id,attempt_id,generation)=(i.owner_id,i.run_id,i.attempt_id,i.generation);
 IF NOT FOUND OR jsonb_typeof(child->'controller') IS DISTINCT FROM 'object'
 OR (child#>>'{controller,host_pid}')::integer=x.host_pid
 OR child#>>'{controller,pid_namespace}' IS DISTINCT FROM x.pid_namespace
 OR child#>>'{controller,cgroup}' IS DISTINCT FROM x.cgroup THEN RETURN false; END IF;
 SELECT * INTO object_row FROM research_objects WHERE id=x.evidence_object_id;
 IF NOT FOUND OR (object_row.owner_id,object_row.object_kind,object_row.sha256)
 IS DISTINCT FROM (i.owner_id,'search_worker_identity'::varchar,x.evidence_sha256)
 OR x.evidence_kind<>'search_worker_identity' THEN RETURN false; END IF;
 SELECT * INTO assigned FROM search_worker_assignments WHERE intent_id=i.id;
 IF NOT FOUND THEN
  RETURN NOT EXISTS(SELECT 1 FROM search_worker_outcomes WHERE intent_id=i.id);
 END IF;
 IF (assigned.owner_id,assigned.run_id,assigned.attempt_id,assigned.generation,assigned.epoch_id,assigned.admission_id,assigned.ticket_id,assigned.expected_identity_id,
 assigned.host_pid,assigned.start_ticks,assigned.pid_namespace,assigned.cgroup)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,a.id,t.id,x.id,x.host_pid,x.start_ticks,x.pid_namespace,x.cgroup)
 THEN RETURN false; END IF;
 -- Single worker: assignments are an exact prefix, every earlier one closed.
 IF (SELECT count(*) FROM search_worker_assignments wa JOIN search_iteration_intents wi ON wi.id=wa.intent_id
  WHERE wa.epoch_id=e.id AND wi.native_iteration<i.native_iteration)<>i.native_iteration-1
 OR EXISTS(SELECT 1 FROM search_worker_assignments wa JOIN search_iteration_intents wi ON wi.id=wa.intent_id
  WHERE wa.epoch_id=e.id AND wi.native_iteration<i.native_iteration
  AND NOT EXISTS(SELECT 1 FROM search_worker_assignment_closures wc WHERE wc.assignment_id=wa.id))
 THEN RETURN false; END IF;
 SELECT * INTO closed FROM search_worker_assignment_closures WHERE assignment_id=assigned.id;
 IF NOT FOUND THEN
  RETURN NOT EXISTS(SELECT 1 FROM search_worker_outcomes WHERE intent_id=i.id);
 END IF;
 SELECT * INTO outcome_row FROM search_worker_outcomes WHERE id=closed.worker_outcome_id;
 IF NOT FOUND OR (closed.owner_id,closed.run_id,closed.attempt_id,closed.generation,closed.epoch_id,closed.intent_id,closed.native_iteration)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,i.id,i.native_iteration)
 OR (outcome_row.owner_id,outcome_row.run_id,outcome_row.attempt_id,outcome_row.generation,outcome_row.epoch_id,outcome_row.intent_id,outcome_row.native_iteration)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,i.id,i.native_iteration)
 OR outcome_row.status NOT IN ('evaluated','worker_error') THEN RETURN false; END IF;
 SELECT * INTO object_row FROM research_objects WHERE id=outcome_row.evidence_object_id;
 IF NOT FOUND OR (object_row.owner_id,object_row.object_kind,object_row.sha256)
 IS DISTINCT FROM (i.owner_id,'search_worker_outcome'::varchar,outcome_row.evidence_sha256)
 OR outcome_row.evidence_kind<>'search_worker_outcome' THEN RETURN false; END IF;
 IF outcome_row.objective_ack_id IS NULL THEN
  RETURN outcome_row.status='worker_error' AND outcome_row.candidate_id IS NULL AND outcome_row.source_sha256 IS NULL;
 END IF;
 SELECT * INTO ack FROM search_objective_acknowledgements WHERE id=outcome_row.objective_ack_id;
 IF NOT FOUND OR (ack.owner_id,ack.run_id,ack.attempt_id,ack.generation,ack.epoch_id,ack.candidate_id,ack.source_sha256)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,outcome_row.candidate_id,outcome_row.source_sha256) THEN RETURN false; END IF;
 SELECT * INTO grant_row FROM search_candidate_grants WHERE id=ack.grant_id;
 IF NOT FOUND OR (grant_row.owner_id,grant_row.run_id,grant_row.attempt_id,grant_row.generation,grant_row.epoch_id,grant_row.candidate_id,grant_row.source_sha256,grant_row.intent_id,grant_row.admission_id)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,outcome_row.candidate_id,outcome_row.source_sha256,i.id,a.id)
 OR grant_row.phase<>'mutation' THEN RETURN false; END IF;
 SELECT * INTO candidate FROM search_candidates WHERE id=outcome_row.candidate_id;
 IF NOT FOUND OR (candidate.owner_id,candidate.run_id,candidate.attempt_id,candidate.generation,candidate.epoch_id,candidate.source_sha256,candidate.parent_candidate_id,candidate.parent_source_sha256)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,outcome_row.source_sha256,i.parent_candidate_id,i.parent_source_sha256)
 OR candidate.kind<>'mutation' THEN RETURN false; END IF;
 SELECT * INTO objective FROM search_objective_receipts WHERE id=ack.objective_receipt_id;
 IF NOT FOUND OR (objective.owner_id,objective.run_id,objective.attempt_id,objective.generation,objective.epoch_id,objective.candidate_id,objective.source_sha256)
 IS DISTINCT FROM (i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,outcome_row.candidate_id,outcome_row.source_sha256)
 OR (objective.status='eligible') IS DISTINCT FROM (outcome_row.status='evaluated') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_worker_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE r record;
BEGIN
 FOR r IN SELECT id FROM search_iteration_intents LOOP
  IF @S@.mineagent_oe_worker_valid(r.id) IS NOT TRUE THEN
   RAISE EXCEPTION 'OE immutable worker assignment closure differs';
  END IF;
 END LOOP;
 -- Reverse check prevents a physical outcome from existing without its atomic
 -- assignment closure; no downstream result is fabricated for a pending tail.
 IF EXISTS(SELECT 1 FROM search_worker_outcomes o WHERE NOT EXISTS(
  SELECT 1 FROM search_worker_assignment_closures c WHERE c.worker_outcome_id=o.id AND c.intent_id=o.intent_id))
 THEN RAISE EXCEPTION 'OE worker outcome has no assignment closure'; END IF;
 RETURN NULL;
END $$;
"""
