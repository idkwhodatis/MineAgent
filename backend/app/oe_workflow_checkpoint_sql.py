"""031 actual stopped OE checkpoint, retaining original RD/local validators."""

from backend.app.oe_workflow_predecessors import predecessor
from backend.app.rdagent_provider_revision import changed

# Explicit copies of the public commitment projections; equivalence tests compare
# these with the actual producer helpers, not reflection over future ORM columns.
MEMBER = (
    "id",
    "iteration_intent_id",
    "transport_id",
    "operation_id",
    "request_sha256",
    "model_binding_sha256",
)
RECEIPT = (
    "id",
    "membership_id",
    "kind",
    "receipt_object_id",
    "receipt_object_kind",
    "receipt_sha256",
    "size_bytes",
)
CONSUMPTION = (
    "id",
    "owner_id",
    "workflow_id",
    "stage_id",
    "input_revision_id",
    "input_sha256",
    "run_id",
    "attempt_id",
    "generation",
    "operation_id",
    "transport_id",
    "request_sha256",
)
APPLICATION = (
    "id",
    "owner_id",
    "run_id",
    "attempt_id",
    "generation",
    "epoch_id",
    "ordinal",
    "control_state_id",
    "worker_outcome_id",
    "outcome",
    "event_sha256",
)


def projection(alias, fields):
    return (
        "jsonb_build_object("
        + ",".join("'" + key + "'," + alias + "." + key for key in fields)
        + ")"
    )


def render_upgrade_sql():
    sql = SQL
    for key, fields in (
        ("@MEMBER@", MEMBER),
        ("@RECEIPT@", RECEIPT),
        ("@CONSUMPTION@", CONSUMPTION),
        ("@APPLICATION@", APPLICATION),
    ):
        sql = sql.replace(key, projection("x", fields))
    original = predecessor("mineagent_workflow_checkpoint")
    anchor = " kind:=CASE s.kind WHEN 'rd_factor_generation'"
    original = changed(
        original,
        anchor,
        " IF s.kind='structured_factor_execution' THEN RETURN mineagent_oe_workflow_checkpoint(o,w,cid); END IF;\n"
        + anchor,
    )
    return sql + original


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_checkpoint(o uuid,w uuid,cid uuid) RETURNS boolean
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE cp record; st record; rev record; q record; epoch record; item record; v jsonb; rid uuid; aid uuid; gen integer; members jsonb; receipts jsonb; consumed jsonb; applied jsonb; refs jsonb;
BEGIN
 SELECT * INTO STRICT cp FROM research_workflow_checkpoints WHERE owner_id=o AND workflow_id=w AND id=cid;
 SELECT * INTO STRICT st FROM research_workflow_stages WHERE owner_id=o AND workflow_id=w AND id=cp.stage_id AND kind='structured_factor_execution';
 SELECT * INTO STRICT rev FROM research_workflow_revisions WHERE owner_id=o AND workflow_id=w AND id=st.input_revision_id;
 SELECT * INTO STRICT q FROM oe_workflow_submissions WHERE owner_id=o AND workflow_id=w AND stage_id=st.id;
 IF mineagent_workflow_raw(cp.terminal_canonical,cp.terminal_sha256,'oe_checkpoint') IS NOT TRUE THEN RETURN false; END IF;
 v:=cp.terminal_canonical::jsonb; rid:=q.run_id; aid:=(v->>'attempt_id')::uuid; gen:=(v->>'generation')::integer;
 IF (v->>'owner_id',v->>'workflow_id',v->>'stage_id',v->>'input_revision_id',v->>'input_sha256',v->>'definition_sha256',v->>'allocation_sha256',v->>'workflow_submission_id',v->>'child_submission_id',v->>'child_run_id')
  IS DISTINCT FROM (o::text,w::text,st.id::text,rev.id::text,rev.inputs_sha256,st.definition_sha256,st.allocation_sha256,q.id::text,q.submission_id::text,rid::text)
 OR st.child_run_id IS DISTINCT FROM rid
 OR NOT EXISTS(SELECT 1 FROM research_runs r JOIN execution_attempts a ON a.run_id=r.id WHERE r.owner_id=o AND r.id=rid AND r.status='succeeded' AND r.engine='openevolve' AND r.purpose='strategy_search' AND r.active_attempt_id=aid AND r.generation=gen AND a.id=aid AND a.generation=gen AND a.status='succeeded' AND a.finished_at IS NOT NULL AND a.sandbox_stopped IS TRUE)
 OR (SELECT count(*) FROM execution_attempts WHERE run_id=rid)<>1 THEN RETURN false; END IF;
 PERFORM mineagent_oe_workflow_attachment(q.id);
 PERFORM mineagent_oes_graph_check(rid);
 SELECT * INTO STRICT epoch FROM oe_structured_epochs WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen;
 IF (v->>'epoch_id',v->>'controller_admission_id') IS DISTINCT FROM (epoch.id::text,epoch.controller_admission_id::text)
 OR mineagent_oes_controller_completed(epoch.id) IS NOT TRUE
 OR NOT EXISTS(SELECT 1 FROM research_process_receipts stop WHERE stop.admission_id=epoch.controller_admission_id AND stop.kind='stop' AND stop.payload_sha256=v->>'controller_stop_sha256') THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(id::text),'[]'::jsonb) INTO refs FROM research_process_admissions WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen;
 IF jsonb_array_length(refs)<>jsonb_array_length(v->'process_admission_ids') OR NOT refs @> (v->'process_admission_ids') OR NOT refs <@ (v->'process_admission_ids') THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM research_process_admissions p WHERE p.owner_id=o AND p.run_id=rid AND (NOT EXISTS(SELECT 1 FROM research_process_states s JOIN research_process_receipts stop ON stop.admission_id=s.admission_id AND stop.kind='stop' WHERE s.admission_id=p.id AND s.status='stopped'))) THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(id::text),'[]'::jsonb) INTO refs FROM research_budget_reservations WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen;
 IF jsonb_array_length(refs)<>jsonb_array_length(v->'reservation_ids') OR NOT refs @> (v->'reservation_ids') OR NOT refs <@ (v->'reservation_ids') THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(id::text),'[]'::jsonb) INTO refs FROM oe_structured_provider_transports WHERE owner_id=o AND run_id=rid AND attempt_id=aid AND generation=gen;
 IF jsonb_array_length(refs)<>jsonb_array_length(v->'provider_transport_ids') OR NOT refs @> (v->'provider_transport_ids') OR NOT refs <@ (v->'provider_transport_ids') THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(@MEMBER@ ORDER BY x.id),'[]'::jsonb) INTO members FROM oe_structured_provider_transports x WHERE x.owner_id=o AND x.run_id=rid AND x.attempt_id=aid AND x.generation=gen;
 SELECT coalesce(jsonb_agg(@RECEIPT@ ORDER BY x.id),'[]'::jsonb) INTO receipts FROM oe_structured_provider_receipts x WHERE x.owner_id=o AND x.run_id=rid AND x.attempt_id=aid AND x.generation=gen;
 IF v->>'provider_receipts_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object('members',members,'receipts',receipts)) THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(@CONSUMPTION@ ORDER BY x.id),'[]'::jsonb) INTO consumed FROM research_workflow_model_consumption x WHERE x.owner_id=o AND x.workflow_id=w AND x.stage_id=st.id;
 IF jsonb_array_length(consumed)<>jsonb_array_length(members) OR v->>'model_consumption_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(consumed) THEN RETURN false; END IF;
 FOR item IN SELECT id FROM research_workflow_model_consumption WHERE owner_id=o AND workflow_id=w AND stage_id=st.id LOOP
  IF mineagent_oe_workflow_consumption(item.id) IS NOT TRUE THEN RETURN false; END IF;
 END LOOP;
 SELECT coalesce(jsonb_agg(@APPLICATION@ ORDER BY x.ordinal),'[]'::jsonb),coalesce(jsonb_agg(x.id::text),'[]'::jsonb) INTO applied,refs FROM oe_structured_terminal_applications x WHERE x.owner_id=o AND x.run_id=rid AND x.attempt_id=aid AND x.generation=gen;
 IF v->>'terminal_applications_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(applied)
 OR jsonb_array_length(refs)<>jsonb_array_length(v->'terminal_application_ids') OR NOT refs @> (v->'terminal_application_ids') OR NOT refs <@ (v->'terminal_application_ids') THEN RETURN false; END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('execution_id',e.id::text,'candidate_id',e.candidate_id::text,'program_version_id',e.program_version_id::text,'result_sha256',z.result_sha256,'measurement_sha256',z.measurement_sha256)),'[]'::jsonb) INTO refs FROM oe_structured_executions e JOIN oe_structured_engine_outcomes z ON z.execution_id=e.id WHERE e.owner_id=o AND e.run_id=rid AND e.attempt_id=aid AND e.generation=gen;
 IF jsonb_array_length(refs)<>jsonb_array_length(v->'executions') OR NOT refs @> (v->'executions') OR NOT refs <@ (v->'executions') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""
