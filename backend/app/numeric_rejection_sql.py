"""Explicit 027 rejection predicates; assembler owns shared enum/version dispatch.

Relational checks cannot inspect MANU2 transport or physical journal files. Host
and cold validation must independently verify those exact retained bytes.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.rdagent_adapter.numeric_profile import CANDIDATE_LIMITS
from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericIntent,
    NumericStop,
)
from packages.research_runtime.numeric_rejection import NumericRejection

TABLES = (
    "research_process_receipts",
    "research_process_states",
    "research_budget_reservations",
    "research_budget_settlements",
)


def render_upgrade_sql():
    rules = []
    for kind, model in (
        ("launch_intent", NumericIntent),
        ("launch_identity", NumericIdentity),
        ("stop", NumericStop),
        ("rejection", NumericRejection),
    ):
        schema = _schema(model)
        _require_serialized_keys(schema)
        literal = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        rules.append(f"WHEN '{kind}' THEN rule := '{literal}'::jsonb;")
    limits = json.dumps(
        dict(CANDIDATE_LIMITS), sort_keys=True, separators=(",", ":")
    ).replace("'", "''")
    return SQL.replace("@RULES@", "\n".join(rules)).replace(
        "@LIMITS@", limits
    ) + "\n".join(
        f"CREATE CONSTRAINT TRIGGER numeric_rejection_graph AFTER INSERT OR UPDATE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_numeric_rejection_trigger();"
        for table in TABLES
    )


def render_downgrade_sql():
    return (
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts WHERE kind='rejection') THEN RAISE EXCEPTION 'Retained numeric rejection blocks downgrade'; END IF; END $$;\n"
        + "\n".join(
            f"DROP TRIGGER numeric_rejection_graph ON @SCHEMA@.{table};"
            for table in TABLES
        )
        + "\nDROP FUNCTION @SCHEMA@.mineagent_numeric_rejection_trigger();\nDROP FUNCTION @SCHEMA@.mineagent_numeric_rejection_stopped(uuid);\nDROP FUNCTION @SCHEMA@.mineagent_numeric_rejection_payload(text,jsonb);"
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_numeric_rejection_payload(kind text,v jsonb) RETURNS boolean
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
CREATE FUNCTION @SCHEMA@.mineagent_numeric_rejection_stopped(aid uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE a record; r record; p record; intent jsonb; ident jsonb; st jsonb; rejection jsonb;
 decl jsonb; launch jsonb; lh text; ih text; sh text; raw text; n integer:=0;
 issued timestamptz; deadline timestamptz; limits jsonb:='@LIMITS@'::jsonb;
BEGIN
 SELECT * INTO a FROM research_process_admissions WHERE id=aid;
 IF NOT FOUND OR a.schema_version IS DISTINCT FROM 'mineagent.process-admission.v2'
 OR a.role IS DISTINCT FROM 'factor_cutoff_candidate' OR a.profile IS DISTINCT FROM 'mineagent.factor-prefix-candidate.v2'
 OR a.ipc_policy IS DISTINCT FROM 'none' OR a.ticket_sha256 IS NOT NULL THEN RETURN false; END IF;
 SELECT * INTO r FROM research_budget_reservations WHERE id=a.reservation_id;
 IF NOT FOUND OR (r.owner_id,r.run_id,r.attempt_id,r.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR r.purpose IS DISTINCT FROM 'process_unit' OR r.state IS DISTINCT FROM 'settled'
 OR ((r.amounts->>'process_slots')::numeric>=1 AND (r.amounts->>'wall_milliseconds')::numeric>=60000
 AND (r.amounts->>'storage_bytes')::numeric>=67174400) IS NOT TRUE
 OR (SELECT count(*) FROM research_budget_settlements b WHERE b.reservation_id=r.id)<>1
 OR NOT EXISTS(SELECT 1 FROM research_budget_settlements b WHERE b.reservation_id=r.id
 AND (b.owner_id,b.run_id,b.attempt_id,b.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
 AND b.outcome='conservative_unknown' AND b.charge=r.amounts) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM research_process_states s WHERE s.admission_id=a.id
 AND (s.owner_id,s.run_id,s.attempt_id,s.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
 AND s.status='stopped') THEN RETURN false; END IF;
 FOR p IN SELECT * FROM research_process_receipts WHERE admission_id=aid LOOP
  n:=n+1;
  IF (p.owner_id,p.run_id,p.attempt_id,p.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
  OR mineagent_numeric_rejection_payload(p.kind,p.payload) IS NOT TRUE
  OR p.payload_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(p.payload) THEN RETURN false; END IF;
  CASE p.kind
   WHEN 'launch_intent' THEN IF intent IS NOT NULL THEN RETURN false; END IF; intent:=p.payload;
   WHEN 'launch_identity' THEN IF ident IS NOT NULL THEN RETURN false; END IF; ident:=p.payload;
   WHEN 'stop' THEN IF st IS NOT NULL THEN RETURN false; END IF; st:=p.payload;
   WHEN 'rejection' THEN IF rejection IS NOT NULL THEN RETURN false; END IF; rejection:=p.payload;
   ELSE RETURN false;
  END CASE;
 END LOOP;
 IF n<>4 OR intent IS NULL OR ident IS NULL OR st IS NULL OR rejection IS NULL THEN RETURN false; END IF;
 decl:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
 'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
 'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
 'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
 'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',null));
 IF intent->'admission' IS DISTINCT FROM decl OR intent->>'id' IS DISTINCT FROM a.id::text
 OR a.input_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(intent->'inputs')
 OR (SELECT jsonb_agg(f->>'destination' ORDER BY ord) FROM jsonb_array_elements(intent->'inputs'->'files') WITH ORDINALITY v(f,ord))
 IS DISTINCT FROM (SELECT jsonb_agg(key ORDER BY key) FROM jsonb_object_keys(limits) AS l(key))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(intent->'inputs'->'files') f WHERE
  ((f->>'size_bytes')::numeric <= (limits->>(f->>'destination'))::numeric) IS NOT TRUE
  OR (f->>'destination'='/input/source.py' AND f->>'sha256' IS DISTINCT FROM intent->'inputs'->>'source_sha256')
  OR (f->>'destination'='/input/manifest.json' AND f->>'sha256' IS DISTINCT FROM intent->'inputs'->>'prefix_sha256')) THEN RETURN false; END IF;
 IF (intent->>'issued_at' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$') IS NOT TRUE
 OR (intent->>'not_after' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$') IS NOT TRUE
 OR (intent->'inputs'->>'cutoff' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$') IS NOT TRUE
 OR (st->>'observed_at' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$') IS NOT TRUE THEN RETURN false; END IF;
 issued:=(intent->>'issued_at')::timestamptz; deadline:=(intent->>'not_after')::timestamptz;
 IF (deadline>issued AND deadline<=issued+interval '10 seconds'
 AND (st->>'observed_at')::timestamptz BETWEEN issued AND deadline+interval '1 second') IS NOT TRUE THEN RETURN false; END IF;
 launch:=jsonb_build_object('schema_version','mineagent.numeric-process-launch.v2','intent',intent,
 'runtime_name','mineagent-numeric-'||aid::text,'root_device',ident->'root_device','root_inode',ident->'root_inode');
 lh:=mineagent_execution_metadata_sha(launch); ih:=mineagent_execution_metadata_sha(ident); sh:=mineagent_execution_metadata_sha(st);
 IF ident->>'launch_sha256' IS DISTINCT FROM lh OR ident->>'admission_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(decl)
 OR ident->>'image_id' IS DISTINCT FROM a.image_id OR st->>'launch_sha256' IS DISTINCT FROM lh
 OR st->>'identity_sha256' IS DISTINCT FROM ih OR st->>'container_id' IS DISTINCT FROM ident->>'container_id'
 OR st->'confirmed_absent' IS DISTINCT FROM 'true'::jsonb OR st->'kernel_identity_absent' IS DISTINCT FROM 'true'::jsonb
 OR st->'deadline_bound_met' IS DISTINCT FROM 'true'::jsonb
 OR rejection->>'launch_sha256' IS DISTINCT FROM lh OR rejection->>'identity_sha256' IS DISTINCT FROM ih
 OR rejection->>'stop_sha256' IS DISTINCT FROM sh OR rejection->>'container_id' IS DISTINCT FROM ident->>'container_id'
 OR rejection->'complete_eof' IS DISTINCT FROM 'true'::jsonb OR rejection->'descendants_reaped' IS DISTINCT FROM 'true'::jsonb
 THEN RETURN false; END IF;
 raw:='{"basename":"result.h5","child_exit_code":'||(rejection->>'child_exit_code')||',"descendants_reaped":true,"schema":"mineagent.numeric-rejection.v1"}';
 IF rejection->>'payload_utf8' IS DISTINCT FROM raw OR rejection->>'payload_sha256' IS DISTINCT FROM encode(sha256(convert_to(raw,'UTF8')),'hex') THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_numeric_rejection_trigger() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE aid uuid; rid uuid;
BEGIN
 IF TG_TABLE_NAME IN ('research_process_receipts','research_process_states') THEN aid:=NEW.admission_id;
 ELSE
  IF TG_TABLE_NAME='research_budget_reservations' THEN rid:=NEW.id; ELSE rid:=NEW.reservation_id; END IF;
  SELECT id INTO aid FROM research_process_admissions WHERE reservation_id=rid;
 END IF;
 IF aid IS NOT NULL AND EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=aid AND kind='rejection')
 AND mineagent_numeric_rejection_stopped(aid) IS NOT TRUE THEN RAISE EXCEPTION 'Numeric rejection requires exact stopped and charged graph'; END IF;
 RETURN NULL;
END $$;
"""
