"""025 exact graph predicates; migration assembler freezes these new literals.

SQL binds original canonical text to receipts and every indexed record. It does
not replace fresh retained-byte reads or claim a database row proves execution.
"""

import json

from backend.app.structured_process_ddl import _require_serialized_keys
from backend.app.structured_schema_sql import structured_schema as _schema
from packages.structured_factor_strategy.contracts import (
    StructuredEngineInput,
    StructuredProgram,
)
from packages.structured_factor_strategy.operator import DecisionRecord
from packages.structured_factor_strategy.run import StructuredRunConfig
from packages.structured_factor_strategy.wire import StructuredResult


def render_contract_sql():
    rules = []
    for name, cls in (
        ("program", StructuredProgram),
        ("config", StructuredRunConfig),
        ("input", StructuredEngineInput),
        ("result", StructuredResult),
        ("decision", DecisionRecord),
    ):
        rule = _schema(cls)
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        rules.append(f"WHEN '{name}' THEN rule := '{raw}'::jsonb;")
    return CONTRACT_SQL.replace("@RULES@", "\n".join(rules))


CONTRACT_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_structured_contract(kind text,v jsonb) RETURNS boolean
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
"""

GRAPH_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_structured_spec_check(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s record; r record; v record; d record; n integer; c jsonb;
BEGIN
 SELECT * INTO s FROM @SCHEMA@.structured_strategy_specs WHERE run_id=rid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured specification missing'; END IF;
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=rid;
 SELECT * INTO v FROM @SCHEMA@.structured_strategy_versions WHERE id=s.structured_strategy_version_id;
 SELECT * INTO d FROM @SCHEMA@.dataset_versions WHERE id=r.dataset_version_id;
 c:=s.config;
 IF (r.owner_id,r.purpose,r.engine,r.generation) IS DISTINCT FROM (s.owner_id,'factor_strategy','structured',1)
 OR r.strategy_version_id IS NOT NULL OR r.config IS DISTINCT FROM c OR r.request_sha256 IS DISTINCT FROM s.config_sha256
 OR v.owner_id IS DISTINCT FROM s.owner_id OR d.owner_id IS DISTINCT FROM s.owner_id
 OR @SCHEMA@.mineagent_structured_contract('program',v.program) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_contract('config',c) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(v.program_canonical,v.program,v.program_sha256,65536) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(s.config_canonical,c,s.config_sha256,1048576) IS NOT TRUE
 OR (c->>'structured_strategy_version_id',c->>'program_sha256',c->>'dataset_version_id',c->>'dataset_sha256',c->>'engine_image_id') IS DISTINCT FROM
 (v.id::text,v.program_sha256,d.id::text,d.content_sha256,r.research_image_digest)
 OR (v.program->'signal_window'->>'end')::date-(v.program->'signal_window'->>'start')::date<>1
 OR c->'execution_window'->>'start' IS DISTINCT FROM v.program->'signal_window'->>'start'
 OR NOT ((c->'execution_window'->>'end')::date>(v.program->'signal_window'->>'end')::date
 AND (c->'execution_window'->>'end')::date-(c->'execution_window'->>'start')::date<=8)
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['threshold','maximum_weight','maximum_gross_weight']) k
 WHERE ((v.program_canonical::json->k)::text ~ '[.e]') IS NOT TRUE)
 OR ((s.config_canonical::json->'initial_cash')::text ~ '[.e]') IS NOT TRUE
 THEN RAISE EXCEPTION 'Structured immutable strategy/spec identity differs'; END IF;
 n:=jsonb_array_length(c->'snapshots');
 IF n<>(SELECT count(DISTINCT x) FROM jsonb_array_elements_text(c->'snapshots') x)
 OR (c->'budget'->>'process_slots')::bigint<n OR (c->'budget'->>'evaluator_executions')::bigint<n
 OR (c->'budget'->>'wall_milliseconds')::bigint<90000*n OR (c->'budget'->>'storage_bytes')::bigint<268500992::bigint*n
 THEN RAISE EXCEPTION 'Structured declared conservative pair budget differs'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_structured_execution_check(eid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e record; s record; v record; p record; done record; r record; x record; j jsonb; raw json; n integer;
BEGIN
 SELECT * INTO e FROM @SCHEMA@.factor_strategy_executions WHERE id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Structured execution missing'; END IF;
 PERFORM @SCHEMA@.mineagent_structured_spec_check(e.run_id);
 SELECT * INTO s FROM @SCHEMA@.structured_strategy_specs WHERE run_id=e.run_id;
 SELECT * INTO v FROM @SCHEMA@.structured_strategy_versions WHERE id=e.structured_strategy_version_id;
 SELECT * INTO p FROM @SCHEMA@.factor_execution_snapshot_publications WHERE id=e.snapshot_id;
 SELECT * INTO done FROM @SCHEMA@.factor_execution_snapshot_outcomes WHERE publication_id=e.snapshot_id;
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=p.run_id;
 j:=e.strategy_input; raw:=e.strategy_input_canonical::json;
 IF (e.owner_id,e.structured_strategy_version_id,e.snapshot_id::text) IS DISTINCT FROM
 (s.owner_id,s.structured_strategy_version_id,s.config->'snapshots'->>e.ordinal)
 OR p.owner_id IS DISTINCT FROM e.owner_id OR done.owner_id IS DISTINCT FROM e.owner_id
 OR e.factor_version_id IS DISTINCT FROM p.factor_version_id
 OR r.owner_id IS DISTINCT FROM e.owner_id OR r.status NOT IN ('succeeded','failed','cancelled','interrupted')
 OR @SCHEMA@.mineagent_structured_contract('input',j) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(e.strategy_input_canonical,j,e.strategy_input_sha256,1048576) IS NOT TRUE
 OR (j->>'owner_id',j->>'run_id',j->>'attempt_id',(j->>'generation')::integer,j->>'execution_id',j->>'structured_strategy_version_id',j->>'market_sha256') IS DISTINCT FROM
 (e.owner_id::text,e.run_id::text,e.attempt_id::text,e.generation,e.id::text,e.structured_strategy_version_id::text,e.market_sha256)
 OR j->'snapshot' IS DISTINCT FROM p.manifest OR j->'program' IS DISTINCT FROM v.program
 OR encode(sha256(convert_to((raw->'snapshot')::text,'UTF8')),'hex') IS DISTINCT FROM done.manifest_sha256
 OR encode(sha256(convert_to((raw->'program')::text,'UTF8')),'hex') IS DISTINCT FROM v.program_sha256
 OR encode(sha256(convert_to((raw->'rows')::text,'UTF8')),'hex') IS DISTINCT FROM done.output_sha256
 OR j->'snapshot'->'input'->'window' IS DISTINCT FROM v.program->'signal_window'
 OR (j->'snapshot'->'input'->'dataset'->>'dataset_version_id',j->'snapshot'->'input'->'dataset'->>'content_sha256') IS DISTINCT FROM
 (s.config->>'dataset_version_id',s.config->>'dataset_sha256')
 OR j->'engine'->'window' IS DISTINCT FROM s.config->'execution_window'
 OR j->'engine'->'initial_cash' IS DISTINCT FROM s.config->'initial_cash'
 OR (j->'engine'->'input_identity'->>'run_config_sha256',j->'engine'->'input_identity'->>'market_data_sha256') IS DISTINCT FROM (v.program_sha256,e.market_sha256)
 OR (j->'engine'->'manifest'->>'strategy_id',j->'engine'->'manifest'->>'code_sha256',j->'engine'->'manifest'->>'parameters_sha256') IS DISTINCT FROM (v.id::text,v.program_sha256,v.program_sha256)
 THEN RAISE EXCEPTION 'Structured completed input binding differs'; END IF;
 PERFORM @SCHEMA@.mineagent_execution_snapshot_check(e.snapshot_id);
 n:=jsonb_array_length(j->'rows');
 IF n<>(p.manifest->>'row_count')::integer OR n<>jsonb_array_length(j->'listing_bindings')
 OR n<>(SELECT count(DISTINCT item->>'listing_id') FROM jsonb_array_elements(j->'rows') item)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(j->'listing_bindings') b
 WHERE b->>'factor_listing_id' IS DISTINCT FROM b->>'native_listing_id'
 OR NOT (j->'snapshot'->'input'->'calculation_universe'->'listing_ids') ? (b->>'factor_listing_id'))
 THEN RAISE EXCEPTION 'Structured complete listing grid differs'; END IF;
 SELECT * INTO x FROM @SCHEMA@.data_usage_exposures WHERE owner_id=e.owner_id AND source_key='structured-factor-source:'||e.run_id::text;
 IF x.id IS NULL OR (x.run_id,x.dataset_version_id,x.purpose) IS DISTINCT FROM
 (e.run_id,(s.config->>'dataset_version_id')::uuid,'structured_factor_execution_source')
 OR x.provenance IS DISTINCT FROM jsonb_build_object('schema_version','mineagent.structured-source-exposure.v1',
 'config_sha256',s.config_sha256,'program_sha256',v.program_sha256,'snapshot_ids',s.config->'snapshots',
 'dataset_manifest_sha256',s.config->>'dataset_sha256','extent','whole_retained_dataset',
 'interpretation','research_input_admission_not_order_or_fill_evidence')
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.dataset_versions d JOIN @SCHEMA@.research_runs owner_run ON owner_run.id=e.run_id
 WHERE d.id=x.dataset_version_id AND d.owner_id=x.owner_id AND x.start_date=d.start_date AND x.end_date=d.end_date
 AND (x.experiment_id,x.split_version_id) IS NOT DISTINCT FROM (owner_run.experiment_id,owner_run.split_version_id))
 THEN RAISE EXCEPTION 'Structured source exposure missing'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_structured_outcome_check(eid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e record; o record; a record; b record; li record; identity record; st record; ex record;
 result jsonb; raw json; item record; stored record; linked jsonb; n integer;
BEGIN
 PERFORM @SCHEMA@.mineagent_structured_execution_check(eid);
 SELECT * INTO e FROM @SCHEMA@.factor_strategy_executions WHERE id=eid;
 SELECT * INTO o FROM @SCHEMA@.factor_strategy_execution_outcomes WHERE execution_id=eid;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO a FROM @SCHEMA@.research_process_admissions WHERE id=o.admission_id;
 SELECT * INTO b FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 SELECT * INTO li FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 SELECT * INTO identity FROM @SCHEMA@.research_process_receipts WHERE id=o.launch_identity_receipt_id;
 SELECT * INTO st FROM @SCHEMA@.research_process_receipts WHERE id=o.stop_receipt_id;
 SELECT * INTO ex FROM @SCHEMA@.research_process_receipts WHERE id=o.extraction_receipt_id;
 result:=o.result; raw:=o.result_canonical::json;
 IF (o.owner_id,o.run_id,o.attempt_id,o.generation) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation)
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.logical_operation_id) IS DISTINCT FROM (e.owner_id,e.run_id,e.attempt_id,e.generation,e.id)
 OR @SCHEMA@.mineagent_structured_stopped(a.id,true) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_contract('result',result) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(o.result_canonical,result,o.result_sha256,16777216) IS NOT TRUE
 OR octet_length(o.result_canonical)<>o.result_size_bytes
 OR (result->>'execution_id',result->>'structured_strategy_version_id',result->>'snapshot_id',result->>'strategy_input_sha256',result->>'market_sha256') IS DISTINCT FROM
 (e.id::text,e.structured_strategy_version_id::text,e.snapshot_id::text,e.strategy_input_sha256,e.market_sha256)
 OR (ex.payload->>'sha256',(ex.payload->>'size_bytes')::bigint,ex.payload->>'strategy_input_sha256',ex.payload->>'market_sha256') IS DISTINCT FROM
 (o.result_sha256,o.result_size_bytes,e.strategy_input_sha256,e.market_sha256)
 OR (identity.payload_sha256,st.payload_sha256,ex.payload_sha256) IS DISTINCT FROM (o.launch_identity_sha256,o.stop_sha256,o.extraction_sha256)
 OR li.payload->'inputs' IS DISTINCT FROM jsonb_build_object('schema_version','mineagent.structured-factor-engine-input-binding.v1',
 'execution_id',e.id,'structured_strategy_version_id',e.structured_strategy_version_id,'snapshot_id',e.snapshot_id,
 'strategy_input_sha256',e.strategy_input_sha256,'strategy_input_size_bytes',e.strategy_input_size_bytes,
 'market_sha256',e.market_sha256,'market_size_bytes',e.market_size_bytes)
 OR b.state IS DISTINCT FROM 'settled' OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements bs
 WHERE bs.reservation_id=b.id AND bs.owner_id=e.owner_id AND bs.run_id=e.run_id AND bs.attempt_id=e.attempt_id AND bs.generation=e.generation
 AND bs.charge=b.amounts AND bs.outcome='conservative_unknown')
 THEN RAISE EXCEPTION 'Structured stopped result commitment differs'; END IF;
 n:=jsonb_array_length(result->'decisions');
 IF n<>o.decision_count OR n<>(SELECT count(*) FROM @SCHEMA@.factor_strategy_decisions WHERE execution_id=eid)
 OR n<>jsonb_array_length(result->'accounting_tables'->'decision_trace')
 OR jsonb_array_length(result->'accounting_tables'->'orders')<>(SELECT count(*) FROM @SCHEMA@.factor_strategy_orders WHERE execution_id=eid)
 OR jsonb_array_length(result->'accounting_tables'->'fills')<>(SELECT count(*) FROM @SCHEMA@.factor_strategy_fills WHERE execution_id=eid)
 THEN RAISE EXCEPTION 'Structured indexed result inventory differs'; END IF;
 FOR item IN SELECT value,ord-1 AS seq FROM jsonb_array_elements(result->'decisions') WITH ORDINALITY a(value,ord) LOOP
  SELECT * INTO stored FROM @SCHEMA@.factor_strategy_decisions WHERE execution_id=eid AND event_seq=item.seq;
  linked:=result->'accounting_tables'->'decision_trace'->item.seq::integer;
  IF stored.execution_id IS NULL OR stored.record IS DISTINCT FROM item.value
  OR stored.record_canonical IS DISTINCT FROM (raw->'decisions'->item.seq::integer)::text
  OR @SCHEMA@.mineagent_structured_raw_commitment(stored.record_canonical,stored.record,stored.record_sha256,1048576) IS NOT TRUE
  OR (stored.observation_sha256,stored.reply_sha256,stored.eligibility_sha256,stored.score_sha256,stored.allocation_sha256,stored.signal_id) IS DISTINCT FROM
  (item.value->>'observation_sha256',item.value->>'reply_sha256',item.value->>'eligibility_sha256',item.value->>'score_sha256',item.value->>'allocation_sha256',item.value->'reply'->'decision'->>'signal_id')
  OR (item.value->>'snapshot_id',item.value->>'program_sha256',(item.value->>'event_seq')::bigint) IS DISTINCT FROM (e.snapshot_id::text,e.strategy_input->'engine'->'input_identity'->>'run_config_sha256',item.seq)
  OR ((linked->>'sequence')::bigint,linked->>'observation_sha256',linked->>'reply_sha256',linked->'decision') IS DISTINCT FROM
  (item.seq,stored.observation_sha256,stored.reply_sha256,item.value->'reply'->'decision')
  THEN RAISE EXCEPTION 'Structured actual read/stage/decision binding differs'; END IF;
 END LOOP;
 FOR item IN SELECT value,ord-1 AS seq FROM jsonb_array_elements(result->'accounting_tables'->'orders') WITH ORDINALITY a(value,ord) LOOP
  SELECT * INTO stored FROM @SCHEMA@.factor_strategy_orders WHERE execution_id=eid AND native_order_id=item.value->>'order_id';
  IF stored.execution_id IS NULL OR stored.record IS DISTINCT FROM item.value
  OR stored.record_canonical IS DISTINCT FROM (raw->'accounting_tables'->'orders'->item.seq::integer)::text
  OR @SCHEMA@.mineagent_structured_raw_commitment(stored.record_canonical,stored.record,stored.record_sha256,65536) IS NOT TRUE
  OR (stored.logical_order_id,stored.listing_id::text) IS DISTINCT FROM (item.value->>'logical_order_id',item.value->>'listing_id')
  OR NOT (result->'accounting_tables'->'decision_trace'->stored.event_seq->'logical_order_ids') ? stored.logical_order_id
  THEN RAISE EXCEPTION 'Structured observed native order is unattributed'; END IF;
 END LOOP;
 FOR item IN SELECT value,ord-1 AS seq FROM jsonb_array_elements(result->'accounting_tables'->'fills') WITH ORDINALITY a(value,ord) LOOP
  SELECT * INTO stored FROM @SCHEMA@.factor_strategy_fills WHERE execution_id=eid AND native_fill_id=item.value->>'fill_id';
  IF stored.execution_id IS NULL OR stored.record IS DISTINCT FROM item.value
  OR stored.record_canonical IS DISTINCT FROM (raw->'accounting_tables'->'fills'->item.seq::integer)::text
  OR @SCHEMA@.mineagent_structured_raw_commitment(stored.record_canonical,stored.record,stored.record_sha256,65536) IS NOT TRUE
  OR stored.native_order_id IS DISTINCT FROM item.value->>'order_id'
  THEN RAISE EXCEPTION 'Structured observed fill differs'; END IF;
 END LOOP;
END $$;
"""

TRIGGER_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_structured_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE scope jsonb; r record; a record;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Structured evidence is immutable'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'Structured READ COMMITTED required'; END IF;
 scope:=to_jsonb(NEW);
 IF TG_TABLE_NAME IN ('factor_strategy_orders','factor_strategy_fills') THEN
  SELECT to_jsonb(e) INTO scope FROM @SCHEMA@.factor_strategy_executions e WHERE id=(scope->>'execution_id')::uuid;
 END IF;
 IF TG_TABLE_NAME='structured_strategy_versions' THEN
  IF @SCHEMA@.mineagent_structured_contract('program',scope->'program') IS NOT TRUE
  OR @SCHEMA@.mineagent_structured_raw_commitment(scope->>'program_canonical',scope->'program',scope->>'program_sha256',65536) IS NOT TRUE
  OR (scope->'program'->'signal_window'->>'end')::date-(scope->'program'->'signal_window'->>'start')::date<>1
  OR (scope->>'parent_version_id')=(scope->>'id')
  OR ((scope->>'parent_version_id') IS NOT NULL AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.structured_strategy_versions p
   WHERE p.id=(scope->>'parent_version_id')::uuid AND p.owner_id=(scope->>'owner_id')::uuid))
  THEN RAISE EXCEPTION 'Structured version requires exact immutable program/parent'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=(scope->>'run_id')::uuid AND owner_id=(scope->>'owner_id')::uuid FOR UPDATE NOWAIT;
 IF NOT FOUND OR (r.purpose,r.engine,r.generation,r.status) IS DISTINCT FROM ('factor_strategy','structured',1,'running')
 THEN RAISE EXCEPTION 'Current structured run required'; END IF;
 IF TG_TABLE_NAME='structured_strategy_specs' THEN RETURN NEW; END IF;
 SELECT * INTO a FROM @SCHEMA@.execution_attempts WHERE id=r.active_attempt_id AND run_id=r.id FOR UPDATE NOWAIT;
 IF NOT FOUND OR (a.id,a.generation,a.status) IS DISTINCT FROM ((scope->>'attempt_id')::uuid,(scope->>'generation')::integer,'running')
 OR a.lease_expires_at IS NULL OR a.lease_expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Current structured attempt required'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_structured_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE value jsonb; rid uuid; ids uuid[]; target uuid; eid uuid; a record; e record; li record; s record; expected jsonb; r record;
BEGIN
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_TABLE_NAME='research_runs' THEN rid:=(value->>'id')::uuid;
 ELSIF value ? 'run_id' THEN rid:=(value->>'run_id')::uuid;
 ELSIF value ? 'execution_id' THEN SELECT run_id INTO rid FROM @SCHEMA@.factor_strategy_executions WHERE id=(value->>'execution_id')::uuid;
 END IF;
 IF rid IS NOT NULL THEN ids:=ARRAY[rid];
 ELSE
  SELECT array_agg(run_id) INTO ids FROM (SELECT run_id FROM @SCHEMA@.structured_strategy_specs
   WHERE owner_id=(value->>'owner_id')::uuid AND
    ((TG_TABLE_NAME='structured_strategy_versions' AND structured_strategy_version_id=(value->>'id')::uuid)
    OR (TG_TABLE_NAME='dataset_versions' AND config->>'dataset_version_id'=value->>'id')) LIMIT 10001) q;
  IF cardinality(ids)>10000 THEN RAISE EXCEPTION 'Structured reverse graph bound'; END IF;
 END IF;
 FOREACH target IN ARRAY coalesce(ids,ARRAY[]::uuid[]) LOOP
  SELECT * INTO s FROM @SCHEMA@.structured_strategy_specs WHERE run_id=target;
  SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=target;
  IF s.run_id IS NULL THEN
   IF (r.purpose,r.engine)=('factor_strategy','structured') THEN RAISE EXCEPTION 'Structured run requires exact specification'; END IF;
   CONTINUE;
  END IF;
  PERFORM @SCHEMA@.mineagent_structured_spec_check(target);
  FOR eid IN SELECT id FROM @SCHEMA@.factor_strategy_executions WHERE run_id=target LOOP
   PERFORM @SCHEMA@.mineagent_structured_outcome_check(eid);
  END LOOP;
  FOR a IN SELECT * FROM @SCHEMA@.research_process_admissions WHERE run_id=target LOOP
   SELECT * INTO e FROM @SCHEMA@.factor_strategy_executions WHERE id=a.logical_operation_id;
   SELECT * INTO li FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
   expected:=jsonb_build_object('schema_version','mineagent.structured-factor-engine-input-binding.v1',
    'execution_id',e.id,'structured_strategy_version_id',e.structured_strategy_version_id,'snapshot_id',e.snapshot_id,
    'strategy_input_sha256',e.strategy_input_sha256,'strategy_input_size_bytes',e.strategy_input_size_bytes,
    'market_sha256',e.market_sha256,'market_size_bytes',e.market_size_bytes);
   IF (a.owner_id,a.run_id,a.attempt_id,a.generation,a.role,a.profile,a.ipc_policy,a.image_id,a.launch_ordinal) IS DISTINCT FROM
    (e.owner_id,e.run_id,e.attempt_id,e.generation,'native_evaluator','mineagent.factor-strategy-engine.v1','none',s.config->>'engine_image_id',1)
    OR a.ticket_sha256 IS NOT NULL OR @SCHEMA@.mineagent_structured_payload('launch_intent',li.payload) IS NOT TRUE
    OR li.payload->'inputs' IS DISTINCT FROM expected
    OR a.input_sha256 IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(expected)
    OR li.payload_sha256 IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(li.payload)
    OR li.payload->'admission' IS DISTINCT FROM jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
    'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,'logical_operation_id',a.logical_operation_id,
    'launch_ordinal',a.launch_ordinal,'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
    'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',null))
   THEN RAISE EXCEPTION 'Structured process cannot bypass exact input declaration'; END IF;
  END LOOP;
  IF r.status='succeeded' AND
   ((SELECT count(*) FROM @SCHEMA@.factor_strategy_execution_outcomes WHERE run_id=target)<>jsonb_array_length(s.config->'snapshots')
   OR EXISTS(SELECT 1 FROM @SCHEMA@.research_process_states WHERE run_id=target AND status<>'stopped'))
   THEN RAISE EXCEPTION 'Structured success requires actual stopped pair outcomes'; END IF;
 END LOOP;
 IF TG_TABLE_NAME='research_process_admissions' AND value->>'profile'='mineagent.factor-strategy-engine.v1'
 AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.structured_strategy_specs WHERE run_id=(value->>'run_id')::uuid AND owner_id=(value->>'owner_id')::uuid)
 THEN RAISE EXCEPTION 'Structured process requires actual structured run'; END IF;
 IF TG_TABLE_NAME IN ('research_model_operations','research_operation_contexts')
 AND EXISTS(SELECT 1 FROM @SCHEMA@.structured_strategy_specs WHERE run_id=rid)
 THEN RAISE EXCEPTION 'Closed structured engine has no model or policy IPC'; END IF;
 IF TG_TABLE_NAME='data_usage_exposures' AND value->>'purpose'='structured_factor_execution_source'
 AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.structured_strategy_specs p WHERE p.run_id=(value->>'run_id')::uuid
 AND p.owner_id=(value->>'owner_id')::uuid AND value->>'source_key'='structured-factor-source:'||p.run_id::text)
 THEN RAISE EXCEPTION 'Structured source exposure cannot be orphaned'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_structured_exposure_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN
  IF OLD.purpose='structured_factor_execution_source' THEN RAISE EXCEPTION 'Structured source exposure immutable'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
 END IF;
 IF NEW.purpose='structured_factor_execution_source' THEN
  PERFORM 1 FROM @SCHEMA@.research_runs r JOIN @SCHEMA@.structured_strategy_specs s ON s.run_id=r.id
   WHERE r.id=NEW.run_id AND r.owner_id=NEW.owner_id AND s.owner_id=NEW.owner_id
   AND NEW.source_key='structured-factor-source:'||r.id::text AND r.status='running'
   AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions a WHERE a.run_id=r.id)
   FOR UPDATE OF r NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION 'Structured exposure must precede process admission'; END IF;
 END IF;
 RETURN NEW;
END $$;
"""
