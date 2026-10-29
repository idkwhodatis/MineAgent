"""027 provider membership SQL, not a retained SDK-file verifier.

The assembler owns run locking, append-only rules and deferred reverse triggers.
TABLES includes dependencies that can invalidate an existing member. Object bytes,
provider usage and actual kernel containment remain live/cold reader obligations.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.openevolve_structured.provider_contracts import (
    OEChatBinding,
    OEChatRequest,
)

TABLES = (
    "oe_structured_specs",
    "oe_structured_iteration_intents",
    "oe_structured_candidates",
    "oe_structured_provider_transports",
    "oe_structured_provider_receipts",
    "oe_structured_provider_exchanges",
    "research_operation_contexts",
    "research_context_aborts",
    "research_model_operations",
    "research_model_transports",
    "research_model_outcomes",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_objects",
)


def render_upgrade_sql():
    rules = []
    for kind, model in (("binding", OEChatBinding), ("request", OEChatRequest)):
        schema = _schema(model)
        _require_serialized_keys(schema)
        raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        rules.append(f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;")
    return SQL.replace("@RULES@", "\n".join(rules))


def render_downgrade_sql():
    return """
DROP FUNCTION @SCHEMA@.mineagent_oes_provider_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_provider_contract(text,jsonb);
"""


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_provider_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false; END CASE;
 RETURN @SCHEMA@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0) IS TRUE;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_provider_check(member_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE m @SCHEMA@.oe_structured_provider_transports%ROWTYPE;
 i @SCHEMA@.oe_structured_iteration_intents%ROWTYPE;
 p @SCHEMA@.oe_structured_candidates%ROWTYPE;
 o @SCHEMA@.research_model_operations%ROWTYPE;
 t @SCHEMA@.research_model_transports%ROWTYPE;
 c @SCHEMA@.research_operation_contexts%ROWTYPE;
 r @SCHEMA@.research_budget_reservations%ROWTYPE;
 s @SCHEMA@.research_budget_settlements%ROWTYPE;
 outcome @SCHEMA@.research_model_outcomes%ROWTYPE;
 e @SCHEMA@.oe_structured_provider_exchanges%ROWTYPE;
 receipt record; binding jsonb; config jsonb; expected jsonb; request jsonb;
 kinds text[]; raw text; config_raw text;
BEGIN
 SELECT * INTO STRICT m FROM @SCHEMA@.oe_structured_provider_transports WHERE id=member_id;
 SELECT * INTO STRICT i FROM @SCHEMA@.oe_structured_iteration_intents WHERE id=m.iteration_intent_id;
 SELECT * INTO STRICT p FROM @SCHEMA@.oe_structured_candidates WHERE id=i.parent_candidate_id;
 SELECT * INTO STRICT o FROM @SCHEMA@.research_model_operations WHERE id=m.operation_id;
 SELECT * INTO STRICT t FROM @SCHEMA@.research_model_transports WHERE id=m.transport_id;
 SELECT * INTO STRICT c FROM @SCHEMA@.research_operation_contexts WHERE id=o.context_id;
 SELECT * INTO STRICT r FROM @SCHEMA@.research_budget_reservations WHERE id=t.reservation_id;
 SELECT sp.config,sp.config_canonical INTO STRICT config,config_raw FROM @SCHEMA@.oe_structured_specs sp
 WHERE sp.owner_id=m.owner_id AND sp.run_id=m.run_id;
 binding:=config->'model_binding'; request:=o.request;
 IF ROW(i.owner_id,i.run_id,i.attempt_id,i.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
 OR ROW(p.owner_id,p.run_id,p.attempt_id,p.generation,p.epoch_id) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation,i.epoch_id)
 OR ROW(o.owner_id,o.run_id,o.attempt_id,o.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
 OR ROW(t.owner_id,t.run_id,t.attempt_id,t.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
 OR ROW(c.owner_id,c.run_id,c.attempt_id,c.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
 OR ROW(r.owner_id,r.run_id,r.attempt_id,r.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
 OR ROW(t.operation_id,t.ordinal,o.protocol,o.purpose,o.ordinal) IS DISTINCT FROM ROW(o.id,1,'mineagent.oe-structured-provider-chat.v1'::text,'chat'::text,0)
 OR (SELECT count(*) FROM @SCHEMA@.research_model_transports WHERE operation_id=o.id)<>1
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_context_aborts WHERE context_id=c.id)
 OR @SCHEMA@.mineagent_oes_provider_contract('binding',binding) IS NOT TRUE
 OR @SCHEMA@.mineagent_oes_provider_contract('request',request) IS NOT TRUE
 OR binding#>>'{pricing,status}' IS DISTINCT FROM 'operator_declared'
 THEN RAISE EXCEPTION 'OE provider scope or protocol'; END IF;
 raw:=@SCHEMA@.mineagent_rd_canonical_json(binding,true);
 IF m.model_binding_sha256 IS DISTINCT FROM encode(sha256(convert_to(raw,'UTF8')),'hex')
 OR request->>'model_binding_sha256' IS DISTINCT FROM m.model_binding_sha256
 OR request->>'model_profile_id' IS DISTINCT FROM binding->>'profile_id'
 OR request->>'model' IS DISTINCT FROM binding->>'model'
 OR (request->>'max_output_tokens')::bigint>(binding->>'max_output_tokens')::bigint
 OR request->>'temperature_millionths' IS DISTINCT FROM '0'
 OR request#>>'{messages,0,role}' IS DISTINCT FROM 'system'
 OR request#>>'{messages,1,role}' IS DISTINCT FROM 'user'
 OR jsonb_array_length(request->'messages')<>2
 OR request#>>'{messages,0,content}' IS DISTINCT FROM i.rendered_canonical::jsonb->>'system'
 OR request#>>'{messages,1,content}' IS DISTINCT FROM i.rendered_canonical::jsonb->>'user'
 OR request->>'parent_candidate_sha256' IS DISTINCT FROM p.candidate_sha256
 OR request->>'rendered_context_sha256' IS DISTINCT FROM i.rendered_sha256
 OR request->>'execution_binding_sha256' IS DISTINCT FROM config#>>'{search,execution_binding_sha256}'
 OR request->>'candidate_domain_sha256' IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_structured_raw_json(config_raw::json#>'{search,domain}'),'UTF8')),'hex')
 OR c.context IS DISTINCT FROM i.context_canonical::jsonb
 OR c.context_sha256 IS DISTINCT FROM i.context_sha256
 OR request->>'context_sha256' IS DISTINCT FROM c.context_sha256
 OR o.request_sha256 IS DISTINCT FROM m.request_sha256
 OR o.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(request,true),'UTF8')),'hex')
 OR o.request_object_kind IS DISTINCT FROM 'oe_structured_provider_request'
 OR octet_length(@SCHEMA@.mineagent_rd_canonical_json(request,true))>262144
 THEN RAISE EXCEPTION 'OE provider exact request'; END IF;
 expected:=jsonb_build_object('calls',1,'tokens',262144+(request->>'max_output_tokens')::bigint,
 'cost_microunits',ceil((262144::numeric*(binding#>>'{pricing,input_microusd_per_million}')::numeric+
 (request->>'max_output_tokens')::numeric*(binding#>>'{pricing,output_microusd_per_million}')::numeric)/1000000),
 'wall_milliseconds',(binding->>'request_deadline_ms')::bigint,'storage_bytes',589824,
 'process_slots',1,'mutation_slots',0,'evaluator_executions',0);
 IF r.purpose IS DISTINCT FROM 'model_transport'
 OR r.logical_key IS DISTINCT FROM 'oe-structured-provider:'||i.id::text
 OR r.amounts IS DISTINCT FROM expected
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_objects obj WHERE obj.owner_id=m.owner_id
 AND obj.id=o.request_object_id AND obj.object_kind=o.request_object_kind AND obj.sha256=o.request_sha256)
 THEN RAISE EXCEPTION 'OE provider reservation/request object'; END IF;
 SELECT array_agg(kind ORDER BY kind) INTO kinds FROM @SCHEMA@.oe_structured_provider_receipts WHERE membership_id=m.id;
 IF coalesce(cardinality(kinds),0)>3 OR (cardinality(kinds)>0 AND NOT 'intent'=ANY(kinds))
 OR ('stopped'=ANY(kinds) AND NOT 'started'=ANY(kinds))
 THEN RAISE EXCEPTION 'OE provider receipt prefix'; END IF;
 FOR receipt IN SELECT * FROM @SCHEMA@.oe_structured_provider_receipts WHERE membership_id=m.id LOOP
  IF ROW(receipt.owner_id,receipt.run_id,receipt.attempt_id,receipt.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_objects obj WHERE obj.owner_id=m.owner_id
   AND obj.id=receipt.receipt_object_id AND obj.object_kind='oe_structured_provider_receipt'
   AND obj.sha256=receipt.receipt_sha256 AND obj.size_bytes=receipt.size_bytes)
  THEN RAISE EXCEPTION 'OE provider receipt object'; END IF;
 END LOOP;
 SELECT * INTO s FROM @SCHEMA@.research_budget_settlements WHERE reservation_id=r.id;
 IF r.state='settled' THEN
  IF s.id IS NULL OR ROW(s.owner_id,s.run_id,s.attempt_id,s.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
  OR s.outcome IS DISTINCT FROM 'conservative_unknown' OR s.charge IS DISTINCT FROM expected
  OR kinds IS DISTINCT FROM ARRAY['intent','started','stopped']::text[]
  THEN RAISE EXCEPTION 'OE provider settlement'; END IF;
 ELSIF s.id IS NOT NULL THEN RAISE EXCEPTION 'OE provider premature settlement'; END IF;
 SELECT * INTO outcome FROM @SCHEMA@.research_model_outcomes WHERE transport_id=t.id;
 IF (r.state='settled') IS DISTINCT FROM (outcome.id IS NOT NULL)
 THEN RAISE EXCEPTION 'OE atomic provider settlement outcome'; END IF;
 IF t.state IN ('completed','unknown') OR o.state IN ('completed','unknown') THEN
  IF outcome.id IS NULL OR t.state IS DISTINCT FROM o.state OR outcome.outcome IS DISTINCT FROM t.state
  THEN RAISE EXCEPTION 'OE terminal transport outcome'; END IF;
 END IF;
 IF outcome.id IS NOT NULL THEN
  IF ROW(outcome.owner_id,outcome.run_id,outcome.attempt_id,outcome.generation) IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation)
  OR outcome.usage IS DISTINCT FROM expected OR r.state IS DISTINCT FROM 'settled'
  OR outcome.outcome NOT IN ('completed','unknown') OR t.state IS DISTINCT FROM outcome.outcome
  OR o.state IS DISTINCT FROM outcome.outcome
  OR (outcome.outcome='unknown' AND ROW(outcome.response_object_id,outcome.response_object_kind,outcome.response_sha256) IS DISTINCT FROM ROW(NULL::uuid,NULL::text,NULL::text))
  THEN RAISE EXCEPTION 'OE provider outcome accounting'; END IF;
 END IF;
 IF outcome.id IS NOT NULL AND (SELECT count(*) FROM @SCHEMA@.oe_structured_provider_exchanges WHERE provider_transport_id=m.id)
  IS DISTINCT FROM (CASE WHEN outcome.outcome='completed' THEN 1::bigint ELSE 0::bigint END)
 THEN RAISE EXCEPTION 'OE exact exchange outcome consumer'; END IF;
 FOR e IN SELECT * FROM @SCHEMA@.oe_structured_provider_exchanges WHERE provider_transport_id=m.id LOOP
  IF ROW(e.owner_id,e.run_id,e.attempt_id,e.generation,e.epoch_id,e.iteration_intent_id,e.parent_candidate_id)
   IS DISTINCT FROM ROW(m.owner_id,m.run_id,m.attempt_id,m.generation,i.epoch_id,i.id,p.id)
  OR t.state IS DISTINCT FROM 'completed' OR o.state IS DISTINCT FROM 'completed'
  OR outcome.id IS NULL OR outcome.outcome IS DISTINCT FROM 'completed'
  OR ROW(e.request_object_id,e.request_kind,e.request_sha256) IS DISTINCT FROM ROW(o.request_object_id,o.request_object_kind,o.request_sha256)
  OR ROW(e.response_object_id,e.response_kind,e.response_sha256) IS DISTINCT FROM ROW(outcome.response_object_id,outcome.response_object_kind,outcome.response_sha256)
  OR e.response_kind IS DISTINCT FROM 'oe_structured_provider_response'
  OR e.context IS DISTINCT FROM c.context OR e.context_sha256 IS DISTINCT FROM c.context_sha256
  OR @SCHEMA@.mineagent_structured_raw_commitment(e.context_canonical,e.context,e.context_sha256,65536) IS NOT TRUE
  THEN RAISE EXCEPTION 'OE provider exchange consumer'; END IF;
 END LOOP;
END $$;
"""
