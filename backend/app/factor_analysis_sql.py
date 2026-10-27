"""022-only semantic fragments; migration freezes literals, never imports app code."""

PLAN_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_analysis_plan_check(pid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE p @SCHEMA@.factor_analysis_plans%ROWTYPE;
 s @SCHEMA@.factor_research_specs%ROWTYPE;
 r @SCHEMA@.research_runs%ROWTYPE;
 d @SCHEMA@.dataset_versions%ROWTYPE;
 o jsonb; lockhash text;
BEGIN
 SELECT * INTO STRICT p FROM @SCHEMA@.factor_analysis_plans WHERE id=pid;
 SELECT * INTO STRICT s FROM @SCHEMA@.factor_research_specs WHERE id=p.spec_id;
 SELECT * INTO STRICT r FROM @SCHEMA@.research_runs WHERE id=p.run_id;
 SELECT * INTO STRICT d FROM @SCHEMA@.dataset_versions WHERE id=p.label_source_dataset_id;
 SELECT value->>'sha256' INTO STRICT lockhash FROM jsonb_array_elements(s.config->'runtime'->'dependency_locks')
 WHERE value->>'role'='analysis';
 o:=p.objective;
 IF (p.owner_id,p.run_id,p.config_sha256,p.input_sha256) IS DISTINCT FROM
 (s.owner_id,s.run_id,s.config_sha256,s.input_sha256)
 OR (r.owner_id,r.purpose,r.engine,r.request_sha256,r.config) IS DISTINCT FROM
 (p.owner_id,'factor_research'::varchar,'rdagent'::varchar,p.config_sha256,s.config)
 OR (d.owner_id,d.content_sha256,d.id::text) IS DISTINCT FROM
 (p.owner_id,p.label_source_manifest_sha256,s.config->'input'->'dataset'->>'dataset_version_id')
 OR p.label_source_manifest_sha256 IS DISTINCT FROM s.config->'input'->'dataset'->>'content_sha256'
 OR p.qlib_image_id IS DISTINCT FROM s.config->'runtime'->>'analysis_image'
 OR p.analysis_dependency_lock_sha256 IS DISTINCT FROM lockhash
 OR @SCHEMA@.mineagent_analysis_raw_commitment(p.objective_canonical,o,p.objective_sha256,65536) IS NOT TRUE
 OR p.source_policy_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(p.source_policy)
 OR p.source_policy IS DISTINCT FROM jsonb_build_object(
 'schema_version','mineagent.rdagent-declared-daily-source.v1',
 'availability','declared_session_close_not_verified','membership','user_fixed_declared_at_source_start',
 'price_basis','unadjusted','acknowledged_unverified',true)
 OR NOT o ?& ARRAY['schema_version','kind','window','label_horizon_sessions','metric','direction','threshold','minimum_defined_days','minimum_pairs','aggregation','selection_claim']
 OR (o-ARRAY['schema_version','kind','window','label_horizon_sessions','metric','direction','threshold','minimum_defined_days','minimum_pairs','aggregation','selection_claim'])<>'{}'::jsonb
 OR (o->>'schema_version',o->>'kind',o->>'aggregation',o->>'selection_claim') IS DISTINCT FROM
 ('mineagent.direct-factor-objective.v1','direct_factor_analysis','arithmetic_mean_of_defined_days','research_selection_not_out_of_sample_performance')
 OR o->'window' IS DISTINCT FROM s.config->'input'->'window'
 OR (o->>'metric' IN ('ic','rank_ic')) IS NOT TRUE
 OR (o->>'direction' IN ('at_least','at_most')) IS NOT TRUE
 OR jsonb_typeof(o->'threshold') IS DISTINCT FROM 'number'
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['label_horizon_sessions','minimum_defined_days','minimum_pairs']) k
 WHERE jsonb_typeof(o->k) IS DISTINCT FROM 'number' OR ((o->>k)~'^[1-9][0-9]*$') IS NOT TRUE)
 THEN RAISE EXCEPTION 'Analysis plan source/spec/objective differs'; END IF;
 IF ((o->>'threshold')::numeric BETWEEN -1 AND 1
 AND (o->>'label_horizon_sessions')::bigint BETWEEN 1 AND 4096
 AND (o->>'minimum_defined_days')::bigint BETWEEN 1 AND 4096
 AND (o->>'minimum_pairs')::bigint BETWEEN 2 AND 10000) IS NOT TRUE
 THEN RAISE EXCEPTION 'Analysis objective exceeds bounded direct protocol'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_analysis_plan_before_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'Analysis plan requires READ COMMITTED'; END IF;
 PERFORM 1 FROM @SCHEMA@.research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM @SCHEMA@.factor_generation_starts WHERE run_id=NEW.run_id)
 THEN RAISE EXCEPTION 'Analysis objective must be frozen before first generation'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_analysis_plan_generation_order() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'Declared generation requires READ COMMITTED'; END IF;
 PERFORM 1 FROM @SCHEMA@.research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'Exact generation owner required'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_analysis_plan_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE pid uuid;
BEGIN
 IF TG_TABLE_NAME='factor_analysis_plans' THEN
  PERFORM @SCHEMA@.mineagent_analysis_plan_check(NEW.id);
 ELSE
  FOR pid IN SELECT p.id FROM @SCHEMA@.factor_analysis_plans p
  WHERE (TG_TABLE_NAME='factor_research_specs' AND p.spec_id=NEW.id)
   OR (TG_TABLE_NAME='research_runs' AND p.run_id=NEW.id)
   OR (TG_TABLE_NAME='dataset_versions' AND p.label_source_dataset_id=NEW.id) LOOP
   PERFORM @SCHEMA@.mineagent_analysis_plan_check(pid);
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
"""


def render_contract_sql():
    import json

    from backend.app.analysis_process_ddl import _require_serialized_keys
    from backend.app.rdagent_receipt_schema_sql import receipt_contract_schema
    from packages.qlib_adapter.contracts import (
        DirectProtocol,
        DirectResult,
        LabelManifest,
    )
    from packages.qlib_adapter.objective import DirectObjectiveMeasurement

    rules = []
    for name, cls in (
        ("protocol", DirectProtocol),
        ("labels", LabelManifest),
        ("result", DirectResult),
        ("measurement", DirectObjectiveMeasurement),
    ):
        rule = receipt_contract_schema(cls, "analysis")
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        rules.append(f"WHEN '{name}' THEN rule := '{raw}'::jsonb;")
    from backend.app.rdagent_receipt_schema_sql import receipt_schema_sql

    return (
        receipt_schema_sql("analysis")
        + "\n"
        + CONTRACT_SQL.replace("@RULES@", "\n".join(rules))
    )


CONTRACT_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_direct_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @SCHEMA@.mineagent_rd_analysis_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""

ANALYSIS_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_direct_intent_check(aid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a @SCHEMA@.factor_direct_analysis_intents%ROWTYPE;
 p @SCHEMA@.factor_analysis_plans%ROWTYPE;
 s @SCHEMA@.factor_execution_snapshot_publications%ROWTYPE;
BEGIN
 SELECT * INTO STRICT a FROM @SCHEMA@.factor_direct_analysis_intents WHERE id=aid;
 SELECT * INTO STRICT p FROM @SCHEMA@.factor_analysis_plans WHERE id=a.plan_id;
 SELECT * INTO STRICT s FROM @SCHEMA@.factor_execution_snapshot_publications WHERE id=a.snapshot_id;
 PERFORM @SCHEMA@.mineagent_analysis_plan_check(p.id);
 PERFORM @SCHEMA@.mineagent_execution_snapshot_check(s.id);
 IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.factor_execution_snapshot_outcomes WHERE publication_id=s.id)
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation) IS DISTINCT FROM
 (s.owner_id,s.run_id,s.attempt_id,s.generation)
 OR (p.owner_id,p.run_id,p.label_source_dataset_id) IS DISTINCT FROM (a.owner_id,a.run_id,a.label_source_id)
 OR NOT @SCHEMA@.mineagent_direct_contract('protocol',a.protocol)
 OR NOT @SCHEMA@.mineagent_direct_contract('labels',a.label_manifest)
 OR a.protocol_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(a.protocol)
 OR a.label_manifest_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(a.label_manifest)
 OR a.protocol->'label_manifest' IS DISTINCT FROM a.label_manifest
 OR a.protocol->>'snapshot_id' IS DISTINCT FROM a.snapshot_id::text
 OR a.protocol->>'snapshot_output_sha256' IS DISTINCT FROM s.manifest->>'output_rows_sha256'
 OR a.protocol->'window' IS DISTINCT FROM p.objective->'window'
 OR a.protocol->'window' IS DISTINCT FROM s.manifest->'input'->'window'
 OR a.label_manifest->>'dataset_id' IS DISTINCT FROM p.label_source_dataset_id::text
 OR a.label_manifest->>'dataset_sha256' IS DISTINCT FROM p.label_source_manifest_sha256
 OR a.label_manifest->'horizon_sessions' IS DISTINCT FROM p.objective->'label_horizon_sessions'
 OR a.label_manifest->>'calendar_sha256' IS DISTINCT FROM s.manifest->'input'->>'calendar_sha256'
 OR a.label_manifest->>'market_timezone' IS DISTINCT FROM s.manifest->'input'->>'market_timezone'
 OR a.label_manifest->>'universe_sha256' IS DISTINCT FROM s.manifest->'input'->'calculation_universe'->>'content_sha256'
 THEN RAISE EXCEPTION 'Direct analysis intent substitutes completed snapshot or labels'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_direct_result_check(aid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a @SCHEMA@.factor_direct_analysis_intents%ROWTYPE;
 p @SCHEMA@.factor_analysis_plans%ROWTYPE;
 o @SCHEMA@.factor_direct_analysis_outcomes%ROWTYPE;
 admission @SCHEMA@.research_process_admissions%ROWTYPE;
 li @SCHEMA@.research_process_receipts%ROWTYPE;
 ident @SCHEMA@.research_process_receipts%ROWTYPE;
 st @SCHEMA@.research_process_receipts%ROWTYPE;
 ex @SCHEMA@.research_process_receipts%ROWTYPE;
 b @SCHEMA@.research_budget_reservations%ROWTYPE;
 measured jsonb; val jsonb; n bigint; pairs bigint; defined_pairs bigint;
 expected_status text; expected_reason text; passes boolean;
BEGIN
 PERFORM @SCHEMA@.mineagent_direct_intent_check(aid);
 SELECT * INTO STRICT a FROM @SCHEMA@.factor_direct_analysis_intents WHERE id=aid;
 SELECT * INTO STRICT p FROM @SCHEMA@.factor_analysis_plans WHERE id=a.plan_id;
 SELECT * INTO STRICT o FROM @SCHEMA@.factor_direct_analysis_outcomes WHERE analysis_id=aid;
 SELECT * INTO STRICT admission FROM @SCHEMA@.research_process_admissions WHERE id=o.admission_id;
 SELECT * INTO STRICT li FROM @SCHEMA@.research_process_receipts WHERE admission_id=o.admission_id AND kind='launch_intent';
 SELECT * INTO STRICT ident FROM @SCHEMA@.research_process_receipts WHERE id=o.launch_identity_receipt_id;
 SELECT * INTO STRICT st FROM @SCHEMA@.research_process_receipts WHERE id=o.stop_receipt_id;
 SELECT * INTO STRICT ex FROM @SCHEMA@.research_process_receipts WHERE id=o.extraction_receipt_id;
 SELECT * INTO STRICT b FROM @SCHEMA@.research_budget_reservations WHERE id=admission.reservation_id;
 IF NOT @SCHEMA@.mineagent_analysis_stopped(admission.id,true)
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation) IS DISTINCT FROM (o.owner_id,o.run_id,o.attempt_id,o.generation)
 OR (a.owner_id,a.run_id,a.attempt_id,a.generation,a.id,p.qlib_image_id) IS DISTINCT FROM
 (admission.owner_id,admission.run_id,admission.attempt_id,admission.generation,admission.logical_operation_id,admission.image_id)
 OR li.payload->'inputs' IS DISTINCT FROM jsonb_build_object(
 'schema_version','mineagent.qlib-analysis-input-binding.v1','analysis_id',a.id,'snapshot_id',a.snapshot_id,
 'label_source_id',a.label_source_id,'protocol_sha256',a.protocol_sha256,
 'analysis_input_sha256',a.analysis_input_sha256,'analysis_input_size_bytes',a.analysis_input_size_bytes)
 OR (ident.payload_sha256,st.payload_sha256,ex.payload_sha256) IS DISTINCT FROM
 (o.launch_identity_receipt_sha256,o.stop_receipt_sha256,o.extraction_receipt_sha256)
 OR NOT @SCHEMA@.mineagent_direct_contract('result',o.result)
 OR NOT @SCHEMA@.mineagent_direct_contract('measurement',o.measurement)
 OR @SCHEMA@.mineagent_analysis_raw_commitment(o.result_canonical,o.result,o.result_sha256,1048576) IS NOT TRUE
 OR o.result_size_bytes<>octet_length(o.result_canonical)
 OR o.result_sha256 IS DISTINCT FROM ex.payload->>'sha256'
 OR o.result_size_bytes IS DISTINCT FROM (ex.payload->>'size_bytes')::bigint
 OR o.result->>'protocol_sha256' IS DISTINCT FROM a.protocol_sha256
 OR @SCHEMA@.mineagent_analysis_raw_commitment(o.measurement_canonical,o.measurement,o.measurement_sha256,65536) IS NOT TRUE
 OR b.state<>'settled' OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements bs
 WHERE bs.reservation_id=b.id AND bs.charge=b.amounts AND bs.outcome='conservative_unknown')
 THEN RAISE EXCEPTION 'Direct analysis outcome lacks exact stopped result and full charge'; END IF;
 SELECT count(*) FILTER(WHERE d->>'reason'='defined'),coalesce(sum((d->>'pair_count')::bigint),0),
 coalesce(sum((d->>'pair_count')::bigint) FILTER(WHERE d->>'reason'='defined'),0)
 INTO n,pairs,defined_pairs FROM jsonb_array_elements(o.result->'days') d;
 IF (o.result->>'defined_days')::bigint<>n OR (o.result->>'total_pairs')::bigint<>pairs
 THEN RAISE EXCEPTION 'Direct series coverage differs'; END IF;
 val:=o.result->(p.objective->>'metric');
 IF n=0 THEN expected_status:='not_evaluated'; expected_reason:='no_defined_days'; val:='null'::jsonb;
 ELSIF n<(p.objective->>'minimum_defined_days')::bigint OR defined_pairs<(p.objective->>'minimum_pairs')::bigint
 THEN expected_status:='not_evaluated'; expected_reason:='insufficient_coverage'; val:='null'::jsonb;
 ELSE
  IF val IS NULL OR val='null'::jsonb THEN RAISE EXCEPTION 'Defined objective needs a value'; END IF;
  passes:=CASE WHEN p.objective->>'direction'='at_least' THEN (val#>>'{}')::numeric >= (p.objective->>'threshold')::numeric
  ELSE (val#>>'{}')::numeric <= (p.objective->>'threshold')::numeric END;
  expected_status:=CASE WHEN passes THEN 'passed' ELSE 'failed' END;
  expected_reason:=CASE WHEN passes THEN 'threshold_satisfied' ELSE 'threshold_not_satisfied' END;
 END IF;
 measured:=jsonb_build_object('schema_version','mineagent.direct-factor-objective-measurement.v1',
 'declaration_sha256',p.objective_sha256,'protocol_sha256',a.protocol_sha256,'result_sha256',o.result_sha256,
 'status',expected_status,'reason',expected_reason,'value',val,'defined_days',n,'total_pairs',pairs,
 'defined_pairs',defined_pairs,'qualification','measurement_not_execution_or_adoption_authority');
 IF o.measurement IS DISTINCT FROM measured THEN RAISE EXCEPTION 'Objective measurement differs from declared threshold'; END IF;
END $$;
"""

CONSISTENCY_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_direct_analysis_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a record; i @SCHEMA@.factor_direct_analysis_intents%ROWTYPE;
 p @SCHEMA@.factor_analysis_plans%ROWTYPE; li @SCHEMA@.research_process_receipts%ROWTYPE;
 analysis_uuid uuid; expected jsonb;
BEGIN
 FOR analysis_uuid IN SELECT t.id FROM @SCHEMA@.factor_direct_analysis_intents t LOOP
  PERFORM @SCHEMA@.mineagent_direct_intent_check(analysis_uuid);
 END LOOP;
 FOR a IN SELECT * FROM @SCHEMA@.research_process_admissions
 WHERE profile='mineagent.qlib-direct-analysis.v1' LOOP
  SELECT * INTO STRICT i FROM @SCHEMA@.factor_direct_analysis_intents WHERE id=a.logical_operation_id;
  SELECT * INTO STRICT p FROM @SCHEMA@.factor_analysis_plans WHERE id=i.plan_id;
  SELECT * INTO STRICT li FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
  expected:=jsonb_build_object('schema_version','mineagent.qlib-analysis-input-binding.v1',
   'analysis_id',i.id,'snapshot_id',i.snapshot_id,'label_source_id',i.label_source_id,
   'protocol_sha256',i.protocol_sha256,'analysis_input_sha256',i.analysis_input_sha256,
   'analysis_input_size_bytes',i.analysis_input_size_bytes);
  IF (a.owner_id,a.run_id,a.attempt_id,a.generation,a.image_id) IS DISTINCT FROM
   (i.owner_id,i.run_id,i.attempt_id,i.generation,p.qlib_image_id)
   OR NOT @SCHEMA@.mineagent_analysis_payload('launch_intent',li.payload)
   OR li.payload->'inputs' IS DISTINCT FROM expected
   OR a.input_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(expected)
   OR li.payload_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(li.payload)
   OR li.payload->'admission' IS DISTINCT FROM jsonb_build_object(
    'schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,'run_id',a.run_id,
    'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
    'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,
    'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,
    'image_id',a.image_id,'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',null))
   OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_reservations b WHERE b.id=a.reservation_id
    AND (b.owner_id,b.run_id,b.attempt_id,b.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
    AND b.purpose='process_unit' AND b.logical_key='direct-analysis:'||i.id::text
    AND (b.amounts->>'evaluator_executions')::numeric>=1 AND (b.amounts->>'process_slots')::numeric>=1
    AND (b.amounts->>'wall_milliseconds')::numeric>=60000 AND (b.amounts->>'storage_bytes')::numeric>=67174400)
   THEN RAISE EXCEPTION 'Analysis process differs from exact declared intent'; END IF;
 END LOOP;
 FOR analysis_uuid IN SELECT analysis_id FROM @SCHEMA@.factor_direct_analysis_outcomes LOOP
  PERFORM @SCHEMA@.mineagent_direct_result_check(analysis_uuid);
 END LOOP;
 RETURN NEW;
END $$;
"""
