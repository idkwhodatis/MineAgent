"""027 spec/controller/control SQL predicates; literal assembler installs later."""

import json

from backend.app.rdagent_controller_ddl import _schema
from backend.app.structured_process_ddl import _require_serialized_keys
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
    runtime_profile_sha256,
)
from runtimes.openevolve_structured.profile import STORAGE_FLOOR, profile_sha256


def render_upgrade_sql():
    rule = _schema(ProviderStructuredBootstrap)
    _require_serialized_keys(rule)
    raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return (
        SQL.replace("@BOOTSTRAP_RULE@", raw)
        .replace("@PHYSICAL_SHA@", profile_sha256())
        .replace("@RUNTIME_SHA@", runtime_profile_sha256())
        .replace("@STORAGE_FLOOR@", str(STORAGE_FLOOR))
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_spec_check(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s record; u record; r record; v record; c jsonb;
BEGIN
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=rid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured search spec missing'; END IF;
 SELECT * INTO u FROM @SCHEMA@.oe_structured_submissions WHERE id=s.submission_id;
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=rid;
 SELECT * INTO v FROM @SCHEMA@.structured_strategy_versions WHERE id=s.seed_program_version_id;
 c:=s.config;
 IF (r.owner_id,r.purpose,r.engine,r.generation) IS DISTINCT FROM (s.owner_id,'strategy_search','openevolve',1)
 OR r.strategy_version_id IS NOT NULL OR r.dataset_version_id IS DISTINCT FROM s.dataset_id
 OR r.config IS DISTINCT FROM c OR r.request_sha256 IS DISTINCT FROM s.config_sha256
 OR (u.owner_id,u.run_id,u.request_sha256,u.preflight_sha256,u.config_sha256,u.idempotency_key,u.request_canonical) IS DISTINCT FROM
 (s.owner_id,s.run_id,s.request_sha256,s.preflight_sha256,s.config_sha256,r.idempotency_key,s.request_canonical)
 OR @SCHEMA@.mineagent_oes_contract('config',c) IS NOT TRUE
 OR @SCHEMA@.mineagent_oes_contract('request',s.request) IS NOT TRUE
 OR (s.request->>'snapshot_id',s.request->'objective',s.request->'budget',s.request->'maximum_mutation_slots',s.request->>'model_profile_id') IS DISTINCT FROM
 (s.snapshot_id::text,c->'search'->'objective',c->'budget',c->'search'->'maximum_mutation_slots',c->'model_binding'->>'profile_id')
 OR s.request->>'model_binding_sha256' IS DISTINCT FROM encode(sha256(convert_to(((s.config_canonical::json)->'model_binding')::text,'UTF8')),'hex')
 OR c->'search'->>'execution_binding_id' IS DISTINCT FROM s.run_id::text
 OR c->'search'->>'execution_binding_sha256' IS DISTINCT FROM encode(sha256(convert_to(((s.config_canonical::json)->'fixed_inputs')::text,'UTF8')),'hex')
 OR s.request->'seed_parameters' IS DISTINCT FROM jsonb_build_object('threshold',v.program->'threshold','top_k',v.program->'top_k','maximum_weight',v.program->'maximum_weight','maximum_gross_weight',v.program->'maximum_gross_weight')
 OR @SCHEMA@.mineagent_structured_raw_commitment(s.request_canonical,s.request,s.request_sha256,65536) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(s.config_canonical,c,s.config_sha256,1048576) IS NOT TRUE
 OR (c->'search'->>'owner_id',c->'search'->>'run_id',c->'fixed_inputs'->>'snapshot_id',c->'fixed_inputs'->>'dataset_version_id',c->>'authenticated_request_sha256') IS DISTINCT FROM
 (s.owner_id::text,s.run_id::text,s.snapshot_id::text,s.dataset_id::text,s.request_sha256)
 OR v.owner_id IS DISTINCT FROM s.owner_id OR v.program IS DISTINCT FROM c->'search'->'domain'->'seed_program'
 OR @SCHEMA@.mineagent_structured_raw_commitment(v.program_canonical,v.program,v.program_sha256,65536) IS NOT TRUE
 OR r.research_image_digest IS DISTINCT FROM c->'runtime'->>'controller_image_id'
 THEN RAISE EXCEPTION 'Structured authenticated declaration differs'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_epoch_check(eid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e record; s record; a record; b record; j jsonb; rule jsonb:='@BOOTSTRAP_RULE@'::jsonb;
BEGIN
 SELECT * INTO e FROM @SCHEMA@.oe_structured_epochs WHERE id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured controller epoch missing'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_spec_check(e.run_id);
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=e.run_id;
 SELECT * INTO a FROM @SCHEMA@.research_process_admissions WHERE id=e.controller_admission_id;
 SELECT * INTO b FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 j:=e.bootstrap_canonical::jsonb;
 IF @SCHEMA@.mineagent_rd_schema(j,rule,coalesce(rule->'$defs','{}'::jsonb),0) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(e.bootstrap_canonical,j,e.bootstrap_sha256,262144) IS NOT TRUE
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.logical_operation_id,a.role,a.profile,a.schema_version,a.ipc_policy,a.launch_ordinal) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,e.id,'openevolve_orchestrator','mineagent.oe-structured-orchestrator.v1','mineagent.process-admission.v2','host_ticket',1)
 OR a.ticket_sha256 IS NULL OR a.image_id IS DISTINCT FROM s.config->'runtime'->>'controller_image_id'
 OR (j->>'admission_id',j->>'epoch_id',j->>'physical_profile_sha256',j->>'runtime_profile_sha256',j->>'adapter_patch_sha256') IS DISTINCT FROM
 (a.id::text,e.id::text,'@PHYSICAL_SHA@','@RUNTIME_SHA@',s.config->'runtime'->>'controller_inventory_sha256')
 OR j->'spec' IS DISTINCT FROM s.config->'search' OR j->'model_binding' IS DISTINCT FROM s.config->'model_binding'
 OR j->>'model_binding_sha256' IS DISTINCT FROM encode(sha256(convert_to(((s.config_canonical::json)->'model_binding')::text,'UTF8')),'hex')
 OR (j->>'not_after')::timestamptz-(j->>'issued_at')::timestamptz IS DISTINCT FROM interval '600 seconds'
 OR (b.owner_id,b.run_id,b.attempt_id,b.generation,b.purpose,b.logical_key) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,'process_unit','oe-structured-controller:'||e.id::text)
 OR b.amounts IS DISTINCT FROM jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'mutation_slots',0,'evaluator_executions',0,'wall_milliseconds',600000,'storage_bytes',@STORAGE_FLOOR@,'process_slots',1)
 THEN RAISE EXCEPTION 'Structured controller declaration/admission differs'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_control_check(cid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c record; p record; s record;
BEGIN
 SELECT * INTO c FROM @SCHEMA@.oe_structured_control_states WHERE id=cid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured native control missing'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_epoch_check(c.epoch_id);
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=c.run_id;
 SELECT * INTO p FROM @SCHEMA@.oe_structured_control_states WHERE id=c.previous_id;
 IF c.sequence>2*(s.config->'search'->>'maximum_mutation_slots')::integer+1
 OR c.frontier IS DISTINCT FROM (c.sequence-1)/2
 OR c.phase IS DISTINCT FROM (CASE WHEN c.sequence=1 THEN 'seed' WHEN c.sequence%2=0 THEN 'sampling' ELSE 'applied' END)
 OR @SCHEMA@.mineagent_structured_raw_commitment(c.state_canonical,c.state_canonical::jsonb,c.state_sha256,786432) IS NOT TRUE
 OR (c.sequence>1 AND (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id,p.sequence) IS DISTINCT FROM
 (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id,c.sequence-1))
 OR (c.sequence=1 AND c.previous_id IS NOT NULL)
 THEN RAISE EXCEPTION 'Structured contiguous native control differs'; END IF;
END $$;
"""


def render_downgrade_sql():
    return """DROP FUNCTION @SCHEMA@.mineagent_oes_control_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_epoch_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_spec_check(uuid);"""
