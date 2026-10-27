"""026 provider relational admission; no claim of reading SDK receipt files.

The assembler installs the mapped receipt table and versioned shared gates first.
This fragment binds full requests, model/steer commitments, authenticated RD call
membership and charged object references. Host/cold readers independently parse
actual SDK identity/stop bytes and reconstruct the exact first prompt. SQL does
not claim that an object reference alone proves containment or prompt consumption.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_provider.contracts import (
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderEmbeddingRequest,
)

TABLES = (
    "research_runs",
    "execution_attempts",
    "rd_provider_transport_receipts",
    "research_model_operations",
    "factor_generation_model_operations",
    "research_model_transports",
    "research_model_outcomes",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_object_publications",
    "research_object_outcomes",
    "factor_generation_starts",
    "factor_research_specs",
)


def render_upgrade_sql():
    rules = []
    for kind, cls in (
        ("config", ProviderFactorResearchConfig),
        ("binding", RDModelBinding),
        ("chat", RDProviderChatRequest),
        ("embedding", RDProviderEmbeddingRequest),
    ):
        schema = _schema(cls)
        if kind == "config":
            # The inherited cross-field budget validator is selected by title
            # in the frozen SQL schema interpreter. This does not change any
            # persisted schema/model-profile value or remove new properties.
            schema["title"] = "FactorResearchConfig"
        _require_serialized_keys(schema)
        raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        rules.append(f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;")
    return SQL.replace("@RULES@", "\n".join(rules)) + "\n".join(
        f"CREATE CONSTRAINT TRIGGER rd_provider_graph AFTER INSERT OR UPDATE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_provider_graph_trigger();"
        for table in TABLES
    )


def render_downgrade_sql():
    return (
        r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.rd_provider_transport_receipts)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_model_operations WHERE protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'))
 THEN RAISE EXCEPTION 'Provider evidence blocks downgrade'; END IF;
END $$;
"""
        + "\n".join(
            f"DROP TRIGGER rd_provider_graph ON @SCHEMA@.{table};" for table in TABLES
        )
        + r"""
DROP TRIGGER rd_provider_receipt_append ON @SCHEMA@.rd_provider_transport_receipts;
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_receipt_append();
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_graph_trigger();
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_graph(uuid);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_reservation(jsonb,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_request_binding(jsonb,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_config(jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_payload(text,jsonb);
"""
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_config(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE x jsonb;
BEGIN
 IF mineagent_rd_provider_payload('config',v) IS NOT TRUE
 OR octet_length(mineagent_rd_canonical_json(v,true))>1048576
 OR octet_length(mineagent_rd_canonical_json(v->'steer_snapshot',true))>32768
 OR v->>'steer_snapshot_sha256' IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(v->'steer_snapshot',true),'UTF8')),'hex')
 THEN RETURN false; END IF;
 IF (SELECT count(DISTINCT value->>'version_id') FROM jsonb_array_elements(v->'steer_snapshot'->'versions'))<>jsonb_array_length(v->'steer_snapshot'->'versions')
 OR (SELECT count(DISTINCT value->>'steer_id') FROM jsonb_array_elements(v->'steer_snapshot'->'versions'))<>jsonb_array_length(v->'steer_snapshot'->'versions')
 THEN RETURN false; END IF;
 FOR x IN SELECT value FROM jsonb_array_elements(v->'steer_snapshot'->'versions') LOOP
  IF length(btrim(x->>'text'))=0 OR octet_length(x->>'text')>8192
  OR x->>'text_sha256' IS DISTINCT FROM encode(sha256(convert_to(x->>'text','UTF8')),'hex')
  THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_request_binding(v jsonb,b jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF mineagent_rd_provider_payload('binding',b) IS NOT TRUE
 OR mineagent_rd_provider_payload(v->>'kind',v) IS NOT TRUE
 THEN RETURN false; END IF;
 RETURN COALESCE(
  v->>'schema_version'='mineagent.rdagent-provider-'||(v->>'kind')||'.v1'
  AND v->>'model_profile_id'=b->>'profile_id'
  AND v->>'model_binding_sha256'=encode(sha256(convert_to(mineagent_rd_canonical_json(b,true),'UTF8')),'hex')
  AND octet_length(mineagent_rd_canonical_json(v,false))<=262144
  AND octet_length(mineagent_rd_canonical_json(v,true))<=(b->>'max_request_bytes')::bigint
  AND CASE WHEN v->>'kind'='chat' THEN
    v->>'model'=b->>'chat_model' AND (v->>'max_output_tokens')::bigint<=(b->>'max_output_tokens')::bigint
   ELSE v->>'model'=b->>'embedding_model' AND v->'dimensions'=b->'embedding_dimensions'
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(v->'inputs') value WHERE length(value) NOT BETWEEN 1 AND 8192)
  END,false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_reservation(v jsonb,b jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE output_tokens bigint:=0; cost numeric;
BEGIN
 IF mineagent_rd_provider_request_binding(v,b) IS NOT TRUE
 OR b->'pricing'->>'status' IS DISTINCT FROM 'operator_declared' THEN RETURN NULL; END IF;
 IF v->>'kind'='chat' THEN
  output_tokens:=(v->>'max_output_tokens')::bigint;
  cost:=262144::numeric*(b->'pricing'->>'chat_input_microusd_per_million')::numeric
    +output_tokens::numeric*(b->'pricing'->>'chat_output_microusd_per_million')::numeric;
 ELSE cost:=262144::numeric*(b->'pricing'->>'embedding_microusd_per_million')::numeric;
 END IF;
 RETURN jsonb_build_object('calls',1,'tokens',262144+output_tokens,
   'cost_microunits',ceil(cost/1000000)::bigint,'mutation_slots',0,'evaluator_executions',0,
   'wall_milliseconds',(b->>'request_deadline_ms')::bigint,'storage_bytes',589824,'process_slots',1);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_receipt_append() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Provider receipt membership is immutable'; END IF;
 PERFORM 1 FROM research_runs r JOIN execution_attempts a ON a.id=r.active_attempt_id
 WHERE r.id=NEW.run_id AND r.owner_id=NEW.owner_id AND r.purpose='factor_research' AND r.engine='rdagent'
 AND r.generation=NEW.generation AND a.id=NEW.attempt_id AND a.run_id=r.id AND a.generation=r.generation
 AND r.status IN ('preparing','running','finalizing') AND a.status IN ('preparing','running','finalizing')
 AND a.finished_at IS NULL AND a.lease_expires_at>clock_timestamp() FOR UPDATE OF r NOWAIT;
 IF NOT FOUND THEN RAISE EXCEPTION 'Provider receipt requires current owned attempt'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_provider_receipt_append BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.rd_provider_transport_receipts
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_provider_receipt_append();
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_graph(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE op record; run record; t record; w record; held record; settlement record; outcome record; amount jsonb; selected jsonb; n integer; attempt record; must_be_stopped boolean;
BEGIN
 SELECT * INTO run FROM research_runs WHERE id=rid;
 IF (SELECT count(*) FROM research_model_operations WHERE run_id=rid AND protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'))>32
 THEN RAISE EXCEPTION 'Provider profile permits at most 32 operations per run'; END IF;
 FOR op IN SELECT * FROM research_model_operations WHERE run_id=rid AND protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1') LOOP
  IF run.id IS NULL OR (run.purpose,run.engine) IS DISTINCT FROM ('factor_research','rdagent')
  OR run.owner_id IS DISTINCT FROM op.owner_id
  OR mineagent_rd_provider_config(run.config) IS NOT TRUE
  OR run.config->'steer_snapshot'->>'owner_id' IS DISTINCT FROM op.owner_id::text
  OR run.config->>'steer_snapshot_sha256' IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(run.config->'steer_snapshot',true),'UTF8')),'hex')
  OR mineagent_rd_provider_request_binding(op.request,run.config->'model_binding') IS NOT TRUE
  OR op.protocol IS DISTINCT FROM op.request->>'schema_version'
  OR op.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'Provider operation/config/request identity differs'; END IF;
  FOR selected IN SELECT value FROM jsonb_array_elements(run.config->'steer_snapshot'->'versions') LOOP
   IF NOT EXISTS(SELECT 1 FROM rd_steer_versions sv
    WHERE sv.owner_id=op.owner_id AND sv.id=(selected->>'version_id')::uuid
    AND sv.steer_id=(selected->>'steer_id')::uuid AND sv.ordinal=(selected->>'ordinal')::integer
    AND sv.kind=selected->>'kind' AND sv.text=selected->>'text' AND sv.text_sha256=selected->>'text_sha256'
    AND sv.content_canonical::jsonb=selected
    AND sv.content_sha256=encode(sha256(convert_to(mineagent_rd_canonical_json(selected,true),'UTF8')),'hex'))
   THEN RAISE EXCEPTION 'Provider request selected steer version differs'; END IF;
  END LOOP;
  amount:=mineagent_rd_provider_reservation(op.request,run.config->'model_binding');
  IF amount IS NULL THEN RAISE EXCEPTION 'Provider pricing/reservation is unavailable'; END IF;
  SELECT count(*) INTO n FROM factor_generation_model_operations m
   JOIN factor_upstream_calls c ON c.id=m.call_id
   JOIN factor_generation_starts g ON g.id=m.start_id
   JOIN factor_research_specs s ON s.id=g.spec_id
   JOIN research_operation_contexts ctx ON ctx.id=m.context_id
   WHERE m.operation_id=op.id AND m.protocol=op.protocol
   AND (m.owner_id,m.run_id,m.attempt_id,m.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
   AND (c.owner_id,c.run_id,c.attempt_id,c.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
   AND (g.owner_id,g.run_id,g.attempt_id,g.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
   AND (ctx.owner_id,ctx.run_id,ctx.attempt_id,ctx.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
   AND (s.owner_id,s.run_id)=(op.owner_id,op.run_id) AND s.config=run.config AND s.config_sha256=run.request_sha256
   AND c.start_id=g.id AND c.context_id=m.context_id AND g.context_id=m.context_id AND op.context_id=m.context_id
   AND ctx.context->>'spec_sha256'=s.config_sha256 AND ctx.context->>'input_sha256'=s.input_sha256
   AND c.kind=op.request->>'kind'
   AND EXISTS(SELECT 1 FROM factor_controller_launches l JOIN factor_research_attempt_bindings b ON b.id=l.attempt_binding_id
    JOIN research_process_admissions a ON a.id=l.admission_id
    WHERE (l.owner_id,l.run_id,l.attempt_id,l.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
    AND (b.owner_id,b.run_id,b.attempt_id,b.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
    AND (a.owner_id,a.run_id,a.attempt_id,a.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation)
    AND b.spec_id=s.id AND a.logical_operation_id=b.id
    AND a.image_id=run.config->'runtime'->>'orchestrator_image' AND a.role='rd_orchestrator' AND a.profile='mineagent.rdagent-orchestrator.v2'
    AND l.bootstrap->'config'=run.config AND l.bootstrap->>'config_sha256'=s.config_sha256);
  IF n<>1 OR (SELECT count(*) FROM factor_generation_model_operations WHERE operation_id=op.id)<>1
  THEN RAISE EXCEPTION 'Provider operation must belong to one exact controller call'; END IF;
  SELECT count(*) INTO n FROM research_model_transports WHERE operation_id=op.id;
  IF n<>1 THEN RAISE EXCEPTION 'Provider operation needs exactly one initial transport; cannot automatically retry'; END IF;
  SELECT * INTO attempt FROM execution_attempts WHERE id=op.attempt_id;
  IF attempt.id IS NULL OR (attempt.run_id,attempt.generation) IS DISTINCT FROM (op.run_id,op.generation)
  THEN RAISE EXCEPTION 'Provider operation attempt scope differs'; END IF;
  must_be_stopped:=attempt.sandbox_stopped IS TRUE OR attempt.finished_at IS NOT NULL
   OR attempt.status IN ('succeeded','failed','cancelled','interrupted')
   OR run.status IN ('succeeded','failed','cancelled','interrupted');
  FOR t IN SELECT * FROM research_model_transports WHERE operation_id=op.id LOOP
   IF must_be_stopped AND (t.state IS NULL OR t.state NOT IN ('completed','failed','unknown'))
   THEN RAISE EXCEPTION 'Terminal provider attempt requires terminal charged transport'; END IF;
   SELECT * INTO held FROM research_budget_reservations WHERE id=t.reservation_id;
   IF (t.owner_id,t.run_id,t.attempt_id,t.generation) IS DISTINCT FROM (op.owner_id,op.run_id,op.attempt_id,op.generation)
   OR (held.owner_id,held.run_id,held.attempt_id,held.generation) IS DISTINCT FROM (op.owner_id,op.run_id,op.attempt_id,op.generation)
   OR held.purpose IS DISTINCT FROM 'model_transport' OR held.amounts IS DISTINCT FROM amount OR t.ordinal<>1
   THEN RAISE EXCEPTION 'Provider exact conservative reservation differs'; END IF;
   IF t.state IN ('completed','failed','unknown') THEN
    SELECT * INTO settlement FROM research_budget_settlements WHERE reservation_id=held.id;
    SELECT * INTO outcome FROM research_model_outcomes WHERE transport_id=t.id;
    IF held.state IS DISTINCT FROM 'settled' OR settlement.outcome IS DISTINCT FROM 'conservative_unknown'
    OR settlement.charge IS DISTINCT FROM amount OR outcome.usage IS DISTINCT FROM amount
    OR outcome.outcome IS DISTINCT FROM t.state
    OR (settlement.owner_id,settlement.run_id,settlement.attempt_id,settlement.generation) IS DISTINCT FROM (op.owner_id,op.run_id,op.attempt_id,op.generation)
    OR (outcome.owner_id,outcome.run_id,outcome.attempt_id,outcome.generation) IS DISTINCT FROM (op.owner_id,op.run_id,op.attempt_id,op.generation)
    THEN RAISE EXCEPTION 'Provider terminal usage cannot refund its whole held vector'; END IF;
   END IF;
   IF (t.state='completed' OR must_be_stopped) AND (SELECT array_agg(kind ORDER BY kind) FROM rd_provider_transport_receipts WHERE transport_id=t.id)
      IS DISTINCT FROM ARRAY['intent','started','stopped']::varchar[]
   THEN RAISE EXCEPTION 'Provider completion needs all registered SDK evidence'; END IF;
  END LOOP;
 END LOOP;
 FOR w IN SELECT * FROM rd_provider_transport_receipts WHERE run_id=rid LOOP
  IF NOT EXISTS(SELECT 1 FROM research_model_transports t JOIN research_model_operations op ON op.id=t.operation_id
   JOIN research_objects obj ON obj.id=w.receipt_object_id
   JOIN research_object_outcomes done ON done.object_id=obj.id AND done.owner_id=obj.owner_id AND done.object_kind=obj.object_kind AND done.sha256=obj.sha256
   JOIN research_object_publications pub ON pub.id=done.publication_id
   JOIN research_budget_reservations held ON held.id=pub.budget_reservation_id
   JOIN research_budget_settlements settled ON settled.reservation_id=held.id
   WHERE t.id=w.transport_id AND op.protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1')
   AND (t.owner_id,t.run_id,t.attempt_id,t.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
   AND (op.owner_id,op.run_id,op.attempt_id,op.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
   AND (pub.owner_id,pub.run_id,pub.attempt_id,pub.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
   AND (held.owner_id,held.run_id,held.attempt_id,held.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
   AND (settled.owner_id,settled.run_id,settled.attempt_id,settled.generation)=(w.owner_id,w.run_id,w.attempt_id,w.generation)
   AND w.receipt_object_kind='rd_provider_process_receipt' AND obj.object_kind=w.receipt_object_kind AND obj.owner_id=w.owner_id
   AND obj.sha256=w.receipt_sha256 AND obj.size_bytes=w.size_bytes AND w.size_bytes BETWEEN 1 AND 16384
   AND pub.object_kind=obj.object_kind AND pub.sha256=obj.sha256 AND pub.size_bytes=obj.size_bytes
   AND held.purpose='object_storage' AND held.state='settled' AND settled.outcome='conservative_unknown' AND settled.charge=held.amounts
   AND (held.amounts->>'storage_bytes')::numeric>=pub.peak_bytes)
  THEN RAISE EXCEPTION 'Provider SDK membership needs exact charged published object'; END IF;
 END LOOP;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_graph_trigger() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rid uuid;
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN rid:=NEW.id;
 ELSIF TG_TABLE_NAME='research_object_outcomes' THEN
  SELECT run_id INTO rid FROM research_object_publications WHERE id=NEW.publication_id;
 ELSE rid:=NEW.run_id; END IF;
 IF rid IS NOT NULL THEN PERFORM mineagent_rd_provider_graph(rid); END IF;
 RETURN NULL;
END $$;
"""
