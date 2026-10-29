"""Normal027 closed candidate/engine predicates for literal migration assembly.

These predicates bind retained canonical text and SQL relations. They do not
replace physical journal/input/result reads or attest provider/native execution.
No migration activation occurs on import.
"""

import json

from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from backend.app.rdagent_controller_ddl import _schema
from backend.app.structured_process_ddl import _require_serialized_keys
from packages.openevolve_structured.candidate import CandidateDocument
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig


def render_contract_sql():
    cases = []
    for name, model in (
        ("request", ProviderStructuredSearchRequest),
        ("candidate", CandidateDocument),
        ("measurement", StructuredMeasurement),
        ("config", ProviderStructuredRunConfig),
    ):
        rule = _schema(model)
        if name == "config":
            # 028 is immutable: 030 installs its explicit versioned successor.
            # Do not let the modern Python union rewrite historical SQL shape.
            properties = rule["$defs"]["FixedExecutionInputs"]["properties"]
            for field, legacy, successor in (
                (
                    "schema_version",
                    "mineagent.oe-structured-retained-inputs.v1",
                    "mineagent.oe-structured-retained-inputs.v2",
                ),
                (
                    "market_qualification",
                    "synthetic_by_construction",
                    "user_declaration_unverified",
                ),
            ):
                clause = properties[field]
                if clause.get("enum") != [legacy, successor]:
                    raise ValueError("Exact reviewed retained-input successor required")
                del clause["enum"]
                clause["const"] = legacy
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        cases.append(f"WHEN '{name}' THEN rule := '{raw}'::jsonb;")
    return CONTRACT.replace("@RULES@", "\n".join(cases))


CONTRACT = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @SCHEMA@.mineagent_rd_schema(v,rule,COALESCE(rule->'$defs','{}'::jsonb),0);
END $$;
"""

CANDIDATE = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_candidate_check(cid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c record; s record; v record; seed record; p record; x record; i record; j jsonb;
BEGIN
 SELECT * INTO c FROM @SCHEMA@.oe_structured_candidates WHERE id=cid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured candidate missing'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_spec_check(c.run_id);
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=c.run_id;
 SELECT * INTO v FROM @SCHEMA@.structured_strategy_versions WHERE id=c.program_version_id;
 SELECT * INTO seed FROM @SCHEMA@.structured_strategy_versions WHERE id=s.seed_program_version_id;
 j:=c.candidate_canonical::jsonb;
 IF s.owner_id IS DISTINCT FROM c.owner_id OR v.owner_id IS DISTINCT FROM c.owner_id
 OR @SCHEMA@.mineagent_oes_contract('config',s.config) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(s.config_canonical,s.config,s.config_sha256,1048576) IS NOT TRUE
 OR seed.owner_id IS DISTINCT FROM c.owner_id
 OR @SCHEMA@.mineagent_oes_contract('candidate',j) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_contract('program',v.program) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(c.candidate_canonical,j,c.candidate_sha256,32768) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(v.program_canonical,v.program,v.program_sha256,65536) IS NOT TRUE
 OR c.program_sha256 IS DISTINCT FROM v.program_sha256
 OR j->'program' IS DISTINCT FROM v.program
 OR encode(sha256(convert_to(((c.candidate_canonical::json)->'program')::text,'UTF8')),'hex') IS DISTINCT FROM v.program_sha256
 OR (v.program-ARRAY['threshold','top_k','maximum_weight','maximum_gross_weight']) IS DISTINCT FROM
 (seed.program-ARRAY['threshold','top_k','maximum_weight','maximum_gross_weight'])
 OR c.ordinal>(s.config->'search'->>'maximum_mutation_slots')::integer
 THEN RAISE EXCEPTION 'Structured candidate frozen domain differs'; END IF;
 IF c.ordinal=0 THEN
  IF (c.program_version_id,c.candidate_sha256) IS DISTINCT FROM
     (s.seed_program_version_id,s.config->'search'->>'seed_candidate_sha256')
  THEN RAISE EXCEPTION 'Structured original seed differs'; END IF;
 ELSE
  SELECT * INTO p FROM @SCHEMA@.oe_structured_candidates WHERE id=c.parent_candidate_id;
  SELECT * INTO x FROM @SCHEMA@.oe_structured_provider_exchanges WHERE id=c.provider_exchange_id;
  SELECT * INTO i FROM @SCHEMA@.oe_structured_iteration_intents WHERE id=x.iteration_intent_id;
  IF (p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id) IS DISTINCT FROM
     (c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id)
  OR p.ordinal>=c.ordinal
  OR (x.epoch_id,x.parent_candidate_id,i.ordinal,i.parent_candidate_id) IS DISTINCT FROM
     (c.epoch_id,p.id,c.ordinal,p.id)
  OR v.parent_version_id IS DISTINCT FROM p.program_version_id
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_engine_outcomes o WHERE o.candidate_id=p.id)
  THEN RAISE EXCEPTION 'Structured proposal parent/operation differs'; END IF;
 END IF;
END $$;
"""

EXECUTION = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_execution_check(eid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e record; c record; s record; v record; p record; done record; j jsonb; raw json;
BEGIN
 SELECT * INTO e FROM @SCHEMA@.oe_structured_executions WHERE id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured candidate execution missing'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_candidate_check(e.candidate_id);
 SELECT * INTO c FROM @SCHEMA@.oe_structured_candidates WHERE id=e.candidate_id;
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=e.run_id;
 SELECT * INTO v FROM @SCHEMA@.structured_strategy_versions WHERE id=e.program_version_id;
 SELECT * INTO p FROM @SCHEMA@.factor_execution_snapshot_publications WHERE id=e.snapshot_id;
 SELECT * INTO done FROM @SCHEMA@.factor_execution_snapshot_outcomes WHERE publication_id=e.snapshot_id;
 j:=e.strategy_input; raw:=e.strategy_input_canonical::json;
 IF (e.owner_id,e.run_id,e.attempt_id,e.generation,e.program_version_id,e.snapshot_id) IS DISTINCT FROM
 (c.owner_id,c.run_id,c.attempt_id,c.generation,c.program_version_id,s.snapshot_id)
 OR p.owner_id IS DISTINCT FROM e.owner_id OR done.owner_id IS DISTINCT FROM e.owner_id
 OR @SCHEMA@.mineagent_structured_contract('input',j) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(e.strategy_input_canonical,j,e.strategy_input_sha256,1048576) IS NOT TRUE
 OR octet_length(e.strategy_input_canonical) IS DISTINCT FROM e.strategy_input_size_bytes
 OR (j->>'owner_id',j->>'run_id',j->>'attempt_id',j->>'generation',j->>'execution_id',j->>'structured_strategy_version_id',j->>'market_sha256') IS DISTINCT FROM
 (e.owner_id::text,e.run_id::text,e.attempt_id::text,e.generation::text,e.id::text,e.program_version_id::text,e.market_sha256)
 OR j->'program' IS DISTINCT FROM v.program OR j->'snapshot' IS DISTINCT FROM p.manifest
 OR j->'engine'->'window' IS DISTINCT FROM s.config->'search'->'objective'->'window'
 OR j->'engine'->'initial_cash' IS DISTINCT FROM s.config->'search'->'objective'->'initial_cash'
 OR (e.market_sha256,e.market_size_bytes::text,e.snapshot_id::text) IS DISTINCT FROM
 (s.config->'fixed_inputs'->>'market_sha256',s.config->'fixed_inputs'->>'market_size_bytes',s.config->'fixed_inputs'->>'snapshot_id')
 OR encode(sha256(convert_to((raw->'program')::text,'UTF8')),'hex') IS DISTINCT FROM v.program_sha256
 OR encode(sha256(convert_to((raw->'snapshot')::text,'UTF8')),'hex') IS DISTINCT FROM done.manifest_sha256
 OR encode(sha256(convert_to((raw->'rows')::text,'UTF8')),'hex') IS DISTINCT FROM done.output_sha256
 THEN RAISE EXCEPTION 'Structured actual execution commitment differs'; END IF;
END $$;
"""


MEASUREMENT = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_measurement_matches(result jsonb,objective jsonb,m jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE summary jsonb:=result->'accounting_tables'->'summary'; reason text; score double precision;
BEGIN
 IF jsonb_typeof(summary) IS DISTINCT FROM 'object'
 OR jsonb_typeof(summary->'closed_trade_count') IS DISTINCT FROM 'number'
 OR (summary->>'closed_trade_count') !~ '^[0-9]+$'
 OR (m->'total_return_pct',m->'max_drawdown_pct',m->'closed_trade_count') IS DISTINCT FROM
    (summary->'total_return_pct',summary->'max_drawdown_pct',summary->'closed_trade_count')
 THEN RETURN false; END IF;
 IF jsonb_typeof(summary->'total_return_pct') IS DISTINCT FROM 'number'
 OR jsonb_typeof(summary->'max_drawdown_pct') IS DISTINCT FROM 'number' THEN
  reason:='undefined_accounting';
 ELSE
  IF (summary->>'max_drawdown_pct')::double precision<0 THEN RETURN false; END IF;
  IF objective->>'minimum_closed_trades' IS NOT NULL
  AND (summary->>'closed_trade_count')::bigint<(objective->>'minimum_closed_trades')::bigint THEN
   reason:='minimum_closed_trades';
  ELSE
   BEGIN
    score:=(summary->>'total_return_pct')::double precision-(summary->>'max_drawdown_pct')::double precision;
    reason:='eligible';
   EXCEPTION WHEN numeric_value_out_of_range THEN reason:='undefined_accounting';
   END;
  END IF;
 END IF;
 IF m->>'reason' IS DISTINCT FROM reason
 OR m->'eligible' IS DISTINCT FROM to_jsonb(reason='eligible') THEN RETURN false; END IF;
 IF reason='eligible' THEN
  RETURN jsonb_typeof(m->'combined_score')='number' AND (m->>'combined_score')::double precision=score;
 END IF;
 RETURN m->'combined_score'='null'::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""

OUTCOME = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_outcome_check(oid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE o record; e record; s record; a record; b record; st record; ex record; li record; result jsonb; m jsonb; oh text;
BEGIN
 SELECT * INTO o FROM @SCHEMA@.oe_structured_engine_outcomes WHERE id=oid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured outcome missing'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_execution_check(o.execution_id);
 SELECT * INTO e FROM @SCHEMA@.oe_structured_executions WHERE id=o.execution_id;
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=e.run_id;
 SELECT * INTO a FROM @SCHEMA@.research_process_admissions WHERE id=o.admission_id;
 SELECT * INTO b FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 SELECT * INTO st FROM @SCHEMA@.research_process_receipts WHERE id=o.stop_receipt_id;
 SELECT * INTO ex FROM @SCHEMA@.research_process_receipts WHERE id=o.extraction_receipt_id;
 SELECT * INTO li FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 result:=o.result_canonical::jsonb; m:=o.measurement_canonical::jsonb;
 oh:=encode(sha256(convert_to(((s.config_canonical::json)->'search'->'objective')::text,'UTF8')),'hex');
 IF (o.owner_id,o.run_id,o.attempt_id,o.generation,o.candidate_id) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,e.candidate_id)
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.logical_operation_id,a.launch_ordinal) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,e.id,1)
 OR a.image_id IS DISTINCT FROM s.config->'runtime'->>'engine_image_id'
 OR @SCHEMA@.mineagent_structured_stopped(a.id,true) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_contract('result',result) IS NOT TRUE
 OR @SCHEMA@.mineagent_oes_contract('measurement',m) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(o.result_canonical,result,o.result_sha256,16777216) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(o.measurement_canonical,m,o.measurement_sha256,65536) IS NOT TRUE
 OR octet_length(o.result_canonical) IS DISTINCT FROM o.result_size_bytes
 OR (result->>'execution_id',result->>'structured_strategy_version_id',result->>'snapshot_id',result->>'strategy_input_sha256',result->>'market_sha256') IS DISTINCT FROM
 (e.id::text,e.program_version_id::text,e.snapshot_id::text,e.strategy_input_sha256,e.market_sha256)
 OR (m->>'execution_id',m->>'structured_strategy_version_id',m->>'snapshot_id',m->>'strategy_input_sha256',m->>'market_sha256',m->>'result_sha256',m->>'objective_sha256') IS DISTINCT FROM
 (e.id::text,e.program_version_id::text,e.snapshot_id::text,e.strategy_input_sha256,e.market_sha256,o.result_sha256,oh)
 OR o.objective_sha256 IS DISTINCT FROM oh
 OR @SCHEMA@.mineagent_oes_measurement_matches(result,s.config->'search'->'objective',m) IS NOT TRUE
 OR (st.admission_id,st.kind,st.payload_sha256,ex.admission_id,ex.kind,ex.payload_sha256) IS DISTINCT FROM
 (a.id,'stop',o.stop_receipt_sha256,a.id,'extraction',o.extraction_receipt_sha256)
 OR (ex.payload->>'sha256',(ex.payload->>'size_bytes')::bigint,ex.payload->>'strategy_input_sha256',ex.payload->>'market_sha256') IS DISTINCT FROM
 (o.result_sha256,o.result_size_bytes,e.strategy_input_sha256,e.market_sha256)
 OR li.payload->'inputs' IS DISTINCT FROM jsonb_build_object('schema_version','mineagent.structured-factor-engine-input-binding.v1',
 'execution_id',e.id,'structured_strategy_version_id',e.program_version_id,'snapshot_id',e.snapshot_id,
 'strategy_input_sha256',e.strategy_input_sha256,'strategy_input_size_bytes',e.strategy_input_size_bytes,
 'market_sha256',e.market_sha256,'market_size_bytes',e.market_size_bytes)
 OR b.state IS DISTINCT FROM 'settled' OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements bs
 WHERE bs.reservation_id=b.id AND bs.owner_id=e.owner_id AND bs.run_id=e.run_id AND bs.attempt_id=e.attempt_id AND bs.generation=e.generation
 AND bs.charge=b.amounts AND bs.outcome='conservative_unknown')
 THEN RAISE EXCEPTION 'Structured stopped outcome/measurement commitment differs'; END IF;
END $$;
"""


def render_upgrade_sql():
    return render_contract_sql() + MEASUREMENT + CANDIDATE + EXECUTION + OUTCOME


def render_downgrade_sql():
    return """DROP FUNCTION @SCHEMA@.mineagent_oes_outcome_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_execution_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_candidate_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_measurement_matches(jsonb,jsonb,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_oes_contract(text,jsonb);"""
