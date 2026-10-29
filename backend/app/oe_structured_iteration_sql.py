"""027 partial sampling/placement/rendered-intent SQL boundaries, not live peers."""

import json

from pydantic import TypeAdapter

from backend.app.rdagent_controller_ddl import _schema
from backend.app.structured_process_ddl import _require_serialized_keys
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.host_events import WorkerPlacementObservation
from packages.openevolve_structured.provider_contracts import RenderedPrompt
from runtimes.rdagent_operational.peer import KernelIdentity


class _KernelSchema:
    @staticmethod
    def model_json_schema():
        return TypeAdapter(KernelIdentity).json_schema()


def render_upgrade_sql():
    cases = []
    for name, model in (
        ("placement", WorkerPlacementObservation),
        ("kernel", _KernelSchema),
        ("context", SearchOperationContext),
        ("rendered", RenderedPrompt),
    ):
        rule = _schema(model)
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        cases.append(f"WHEN '{name}' THEN rule := '{raw}'::jsonb;")
    return SQL.replace("@RULES@", "\n".join(cases))


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_iteration_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @SCHEMA@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_sampling_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE si record; c record; previous record; p record; b record; j jsonb; before_state jsonb;
BEGIN
 SELECT * INTO si FROM @SCHEMA@.oe_structured_sampling_intents WHERE id=sid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured sampling intent missing'; END IF;
 SELECT * INTO c FROM @SCHEMA@.oe_structured_control_states WHERE id=si.sampling_state_id;
 SELECT * INTO previous FROM @SCHEMA@.oe_structured_control_states WHERE id=c.previous_id;
 SELECT * INTO p FROM @SCHEMA@.oe_structured_candidates WHERE id=si.parent_candidate_id;
 SELECT * INTO b FROM @SCHEMA@.research_budget_reservations WHERE id=si.mutation_reservation_id;
 PERFORM @SCHEMA@.mineagent_oes_control_check(c.id);
 PERFORM @SCHEMA@.mineagent_oes_population_check(previous.id);
 j:=si.selection_canonical::jsonb; before_state:=previous.state_canonical::jsonb;
 IF (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.sequence,c.frontier,c.phase) IS DISTINCT FROM
 (si.owner_id,si.run_id,si.attempt_id,si.generation,si.epoch_id,2*si.ordinal,si.ordinal-1,'sampling')
 OR (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id) IS DISTINCT FROM
 (si.owner_id,si.run_id,si.attempt_id,si.generation,si.epoch_id)
 OR p.ordinal>=si.ordinal
 OR c.state_canonical::jsonb->'programs' IS DISTINCT FROM before_state->'programs'
 OR @SCHEMA@.mineagent_structured_raw_commitment(si.selection_canonical,j,si.selection_sha256,65536) IS NOT TRUE
 OR (j->>'schema_version',j->>'native_iteration',j->>'parent_id') IS DISTINCT FROM
 ('mineagent.oe-structured-selection.v1',si.ordinal::text,p.upstream_program_id::text)
 OR j->'snapshot'->'programs' IS DISTINCT FROM before_state->'programs'
 OR NOT (before_state->'programs' ? p.upstream_program_id::text)
 OR (j->'snapshot'->>'current_island',j->'snapshot'->>'sampling_island',j->'snapshot'->'feature_dimensions') IS DISTINCT FROM
 ('0','0','["complexity","diversity"]'::jsonb)
 OR (b.owner_id,b.run_id,b.attempt_id,b.generation,b.purpose,b.logical_key) IS DISTINCT FROM
 (si.owner_id,si.run_id,si.attempt_id,si.generation,'engine_mutation','oe-structured-mutation:'||si.epoch_id::text||':'||si.ordinal::text)
 OR b.amounts IS DISTINCT FROM jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'mutation_slots',1,'evaluator_executions',0,'wall_milliseconds',0,'storage_bytes',0,'process_slots',0)
 THEN RAISE EXCEPTION 'Structured actual sampling/parent/hold differs'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_placement_check(pid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE p record; si record; e record; identity record; child record; j jsonb; worker jsonb; binding jsonb;
BEGIN
 SELECT * INTO p FROM @SCHEMA@.oe_structured_worker_placements WHERE id=pid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured worker placement missing'; END IF;
 SELECT * INTO si FROM @SCHEMA@.oe_structured_sampling_intents WHERE id=p.sampling_intent_id;
 PERFORM @SCHEMA@.mineagent_oes_sampling_check(si.id);
 SELECT * INTO e FROM @SCHEMA@.oe_structured_epochs WHERE id=si.epoch_id;
 SELECT * INTO identity FROM @SCHEMA@.research_process_receipts WHERE admission_id=e.controller_admission_id AND kind='launch_identity';
 SELECT * INTO child FROM @SCHEMA@.research_process_receipts WHERE admission_id=e.controller_admission_id AND kind='controller_child';
 j:=p.placement_canonical::jsonb; worker:=p.worker_identity_canonical::jsonb;
 binding:=jsonb_build_object('container_id',identity.payload->'container_id','admission_sha256',identity.payload->'admission_sha256','leader',identity.payload->'leader','controller',child.payload->'controller');
 IF (p.owner_id,p.run_id,p.attempt_id,p.generation) IS DISTINCT FROM (si.owner_id,si.run_id,si.attempt_id,si.generation)
 OR @SCHEMA@.mineagent_oes_iteration_contract('placement',j) IS NOT TRUE
 OR @SCHEMA@.mineagent_oes_iteration_contract('kernel',worker) IS NOT TRUE
 OR (worker->>'host_pid')::bigint NOT BETWEEN 1 AND 2147483647
 OR (worker->>'start_ticks')::bigint<1
 OR @SCHEMA@.mineagent_structured_raw_commitment(p.placement_canonical,j,p.placement_sha256,8192) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(p.worker_identity_canonical,worker,p.worker_identity_sha256,16384) IS NOT TRUE
 OR p.controller_identity_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(binding,true),'UTF8')),'hex')
 OR (j->>'epoch_id',j->>'intent_id',j->>'controller_namespace_pid',j->>'worker_start_ticks') IS DISTINCT FROM
 (e.id::text,si.id::text,child.payload->>'namespace_pid',worker->>'start_ticks')
 OR j->>'controller_namespace_pid'=j->>'worker_namespace_pid'
 OR (worker->>'ppid',worker->>'pid_namespace',worker->>'cgroup',worker->'uids',worker->'gids') IS DISTINCT FROM
 (child.payload->'controller'->>'host_pid',child.payload->'controller'->>'pid_namespace',child.payload->'controller'->>'cgroup','[65533,65533,65533,65533]'::jsonb,'[65533,65533,65533,65533]'::jsonb)
 OR worker->>'host_pid' IN (identity.payload->'leader'->>'host_pid',child.payload->'controller'->>'host_pid')
 THEN RAISE EXCEPTION 'Structured observed worker/controller binding differs'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_iteration_check(iid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE i record; si record; p record; parent record; s record; j jsonb;
BEGIN
 SELECT * INTO i FROM @SCHEMA@.oe_structured_iteration_intents WHERE id=iid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured rendered intent missing'; END IF;
 SELECT * INTO si FROM @SCHEMA@.oe_structured_sampling_intents WHERE id=i.id;
 SELECT * INTO p FROM @SCHEMA@.oe_structured_worker_placements WHERE sampling_intent_id=i.id;
 SELECT * INTO parent FROM @SCHEMA@.oe_structured_candidates WHERE id=i.parent_candidate_id;
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=i.run_id;
 PERFORM @SCHEMA@.mineagent_oes_placement_check(p.id);
 j:=i.context_canonical::jsonb;
 IF (i.owner_id,i.run_id,i.attempt_id,i.generation,i.epoch_id,i.sampling_state_id,i.ordinal,i.parent_candidate_id,i.selection_canonical,i.selection_sha256,i.worker_ticket_sha256) IS DISTINCT FROM
 (si.owner_id,si.run_id,si.attempt_id,si.generation,si.epoch_id,si.sampling_state_id,si.ordinal,si.parent_candidate_id,si.selection_canonical,si.selection_sha256,si.worker_ticket_sha256)
 OR (i.worker_identity_canonical,i.worker_identity_sha256) IS DISTINCT FROM (p.worker_identity_canonical,p.worker_identity_sha256)
 OR @SCHEMA@.mineagent_oes_iteration_contract('context',j) IS NOT TRUE
 OR @SCHEMA@.mineagent_oes_iteration_contract('rendered',i.rendered_canonical::jsonb) IS NOT TRUE
 OR i.rendered_canonical::jsonb->>'current_candidate_sha256' IS DISTINCT FROM parent.candidate_sha256
 OR @SCHEMA@.mineagent_structured_raw_commitment(i.context_canonical,j,i.context_sha256,65536) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(i.rendered_canonical,i.rendered_canonical::jsonb,i.rendered_sha256,262144) IS NOT TRUE
 OR (j->>'owner_id',j->>'run_id',j->>'epoch_id',j->>'iteration_intent_id',j->>'native_iteration',j->>'parent_candidate_id',j->>'parent_candidate_sha256',j->>'execution_binding_sha256',j->>'selection_snapshot_sha256',j->>'rendered_context_sha256') IS DISTINCT FROM
 (i.owner_id::text,i.run_id::text,i.epoch_id::text,i.id::text,i.ordinal::text,parent.id::text,parent.candidate_sha256,s.config->'search'->>'execution_binding_sha256',i.selection_sha256,i.rendered_sha256)
 THEN RAISE EXCEPTION 'Structured completed rendered operation differs'; END IF;
END $$;
"""


def render_downgrade_sql():
    return """DROP FUNCTION @SCHEMA@.mineagent_oes_iteration_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_placement_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_sampling_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_iteration_contract(text,jsonb);"""
