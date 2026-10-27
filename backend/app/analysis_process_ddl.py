"""022 analysis-only SQL fragments; assembler freezes them, never rewrites history.

These checks validate persisted relationships, not physical containment or Qlib
execution. Fresh protected file reads remain mandatory for result use.
"""

import json
import re

from backend.app.rdagent_receipt_schema_sql import receipt_contract_schema
from packages.research_runtime.analysis_process_journal import (
    AnalysisExtraction,
    AnalysisIdentity,
    AnalysisIntent,
    AnalysisStop,
)

PROFILE_GATE = """(schema_version='mineagent.process-admission.v2'
 AND role='native_evaluator' AND profile='mineagent.qlib-direct-analysis.v1'
 AND ipc_policy='none' AND ticket_sha256 IS NULL)"""


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact schema required")
    rules = []
    for kind, cls in (
        ("launch_intent", AnalysisIntent),
        ("launch_identity", AnalysisIdentity),
        ("stop", AnalysisStop),
        ("extraction", AnalysisExtraction),
    ):
        rule = receipt_contract_schema(cls, "analysis")
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        rules.append(f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;")
    return SQL.replace("@S@", schema).replace("@RULES@", "\n".join(rules))


def _require_serialized_keys(node):
    # Pydantic defaults are always emitted by our canonical journal writer.
    # SQL must not accept omitted defaults as an alternate serialized grammar.
    if isinstance(node, dict):
        if "properties" in node:
            node["required"] = sorted(node["properties"])
        for value in node.values():
            _require_serialized_keys(value)
    elif isinstance(node, list):
        for value in node:
            _require_serialized_keys(value)


SQL = r"""
CREATE FUNCTION @S@.mineagent_analysis_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@S@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @S@.mineagent_rd_analysis_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @S@.mineagent_analysis_stopped(aid uuid, require_result boolean) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE a record; p record; intent jsonb; ident jsonb; stopped jsonb; extracted jsonb;
 decl jsonb; launch jsonb; lh text; ih text; issued timestamptz; deadline timestamptz;
 n integer:=0;
BEGIN
 SELECT * INTO a FROM research_process_admissions WHERE id=aid;
 IF NOT FOUND OR a.schema_version<>'mineagent.process-admission.v2'
 OR a.role<>'native_evaluator' OR a.profile<>'mineagent.qlib-direct-analysis.v1'
 OR a.ipc_policy<>'none' OR a.ticket_sha256 IS NOT NULL THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM research_budget_reservations b WHERE b.id=a.reservation_id
 AND b.owner_id=a.owner_id AND b.run_id=a.run_id AND b.attempt_id=a.attempt_id AND b.generation=a.generation
 AND b.purpose='process_unit' AND b.logical_key='direct-analysis:'||a.logical_operation_id::text
 AND (b.amounts->>'evaluator_executions')::numeric>=1 AND (b.amounts->>'process_slots')::numeric>=1
 AND (b.amounts->>'wall_milliseconds')::numeric>=60000 AND (b.amounts->>'storage_bytes')::numeric>=67174400)
 THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM research_process_states s WHERE s.admission_id=a.id
 AND s.owner_id=a.owner_id AND s.run_id=a.run_id AND s.attempt_id=a.attempt_id
 AND s.generation=a.generation AND s.status='stopped') THEN RETURN false; END IF;
 FOR p IN SELECT * FROM research_process_receipts WHERE admission_id=a.id LOOP
  n:=n+1;
  IF p.owner_id<>a.owner_id OR p.run_id<>a.run_id OR p.attempt_id<>a.attempt_id OR p.generation<>a.generation
  OR NOT @S@.mineagent_analysis_payload(p.kind,p.payload)
  OR p.payload_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(p.payload,true),'UTF8')),'hex') THEN RETURN false; END IF;
  CASE p.kind
   WHEN 'launch_intent' THEN IF intent IS NOT NULL THEN RETURN false; END IF; intent:=p.payload;
   WHEN 'launch_identity' THEN IF ident IS NOT NULL THEN RETURN false; END IF; ident:=p.payload;
   WHEN 'stop' THEN IF stopped IS NOT NULL THEN RETURN false; END IF; stopped:=p.payload;
   WHEN 'extraction' THEN IF extracted IS NOT NULL THEN RETURN false; END IF; extracted:=p.payload;
   ELSE RETURN false;
  END CASE;
 END LOOP;
 IF n NOT IN (3,4) OR intent IS NULL OR ident IS NULL OR stopped IS NULL
 OR (require_result AND extracted IS NULL) THEN RETURN false; END IF;
 decl:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
 'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
 'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
 'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
 'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',null));
 IF intent->'admission'<>decl OR intent->>'id'<>a.id::text
 OR intent->'inputs'->>'analysis_id'<>a.logical_operation_id::text
 OR a.input_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(intent->'inputs',true),'UTF8')),'hex')
 OR intent->>'issued_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
 OR intent->>'not_after' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
 OR stopped->>'observed_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$' THEN RETURN false; END IF;
 issued:=(intent->>'issued_at')::timestamptz; deadline:=(intent->>'not_after')::timestamptz;
 IF deadline<=issued OR deadline>issued+interval '10 seconds' THEN RETURN false; END IF;
 launch:=jsonb_build_object('schema_version','mineagent.qlib-analysis-process-launch.v1','intent',intent,
 'runtime_name','mineagent-analysis-'||a.id::text,'root_device',ident->'root_device','root_inode',ident->'root_inode');
 lh:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(launch,true),'UTF8')),'hex');
 ih:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(ident,true),'UTF8')),'hex');
 IF ident->>'launch_sha256'<>lh OR ident->>'image_id'<>a.image_id
 OR ident->>'admission_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(decl,true),'UTF8')),'hex')
 OR stopped->>'launch_sha256'<>lh OR stopped->>'identity_sha256'<>ih
 OR stopped->>'container_id'<>ident->>'container_id' THEN RETURN false; END IF;
 IF stopped->'deadline_bound_met'='true'::jsonb AND NOT ((stopped->>'observed_at')::timestamptz BETWEEN issued AND deadline+interval '1 second') THEN RETURN false; END IF;
 IF extracted IS NOT NULL AND (stopped->'deadline_bound_met'<>'true'::jsonb
 OR extracted->>'launch_sha256'<>lh OR extracted->>'identity_sha256'<>ih
 OR extracted->>'analysis_input_sha256'<>intent->'inputs'->>'analysis_input_sha256'
 OR extracted->>'protocol_sha256'<>intent->'inputs'->>'protocol_sha256') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""
