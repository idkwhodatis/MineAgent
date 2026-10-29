"""027 exact controller journal relations; rows do not prove physical observation.

The host/cold reader must independently reread actual protected journal bytes.
These predicates preserve partial intent/containment states and never equate a
Stop receipt with successful original-deadline completion or a launch grant.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from runtimes.openevolve_structured_host.completion_journal import ControllerCompletion
from runtimes.openevolve_structured_host.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerStop,
)

MODELS = {
    "launch_intent": ControllerIntent,
    "launch_identity": ControllerIdentity,
    "controller_child": ControllerChild,
    "stop": ControllerStop,
    "controller_completion": ControllerCompletion,
}
TABLES = (
    "oe_structured_epochs",
    "oe_structured_specs",
    "research_process_admissions",
    "research_process_states",
    "research_process_receipts",
    "research_budget_reservations",
    "research_budget_settlements",
)


def render_upgrade_sql():
    rules = []
    for kind, model in MODELS.items():
        rule = _schema(model)
        _require_serialized_keys(rule)
        raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace("'", "''")
        rules.append(f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;")
    return SQL.replace("@RULES@", "\n".join(rules))


def render_downgrade_sql():
    return """
DROP FUNCTION @SCHEMA@.mineagent_oes_controller_completed(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_controller_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_controller_payload(text,jsonb);
"""


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_controller_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false; END CASE;
 RETURN @SCHEMA@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_controller_check(epoch_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e @SCHEMA@.oe_structured_epochs%ROWTYPE;
 a @SCHEMA@.research_process_admissions%ROWTYPE;
 st @SCHEMA@.research_process_states%ROWTYPE;
 b @SCHEMA@.research_budget_reservations%ROWTYPE;
 paid @SCHEMA@.research_budget_settlements%ROWTYPE;
 r record; receipts integer:=0;
 intent jsonb; ident jsonb; child jsonb; stopped jsonb; completed jsonb;
 declaration jsonb; inventory jsonb; launch jsonb; bootstrap jsonb;
 admission_sha text; launch_sha text; identity_sha text; child_sha text; stop_sha text;
 started text; quiescent text; seed text; n integer; issued timestamptz; deadline timestamptz;
BEGIN
 PERFORM @SCHEMA@.mineagent_oes_epoch_check(epoch_id);
 SELECT * INTO STRICT e FROM @SCHEMA@.oe_structured_epochs WHERE id=epoch_id;
 SELECT * INTO STRICT a FROM @SCHEMA@.research_process_admissions WHERE id=e.controller_admission_id;
 SELECT * INTO STRICT st FROM @SCHEMA@.research_process_states WHERE admission_id=a.id;
 SELECT * INTO STRICT b FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 IF ROW(st.owner_id,st.run_id,st.attempt_id,st.generation) IS DISTINCT FROM ROW(a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR st.status NOT IN ('launch_intended','uncertain','running','stopped')
 THEN RAISE EXCEPTION 'OE controller state scope'; END IF;
 FOR r IN SELECT * FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id LOOP
  receipts:=receipts+1;
  IF receipts>5 OR ROW(r.owner_id,r.run_id,r.attempt_id,r.generation) IS DISTINCT FROM ROW(a.owner_id,a.run_id,a.attempt_id,a.generation)
  OR @SCHEMA@.mineagent_oes_controller_payload(r.kind,r.payload) IS NOT TRUE
  OR octet_length(@SCHEMA@.mineagent_rd_canonical_json(r.payload,true)) NOT BETWEEN 1 AND 16384
  OR r.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(r.payload,true),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'OE controller typed receipt'; END IF;
  CASE r.kind
   WHEN 'launch_intent' THEN IF intent IS NOT NULL THEN RAISE EXCEPTION 'Duplicate intent'; END IF; intent:=r.payload;
   WHEN 'launch_identity' THEN IF ident IS NOT NULL THEN RAISE EXCEPTION 'Duplicate identity'; END IF; ident:=r.payload;
   WHEN 'controller_child' THEN IF child IS NOT NULL THEN RAISE EXCEPTION 'Duplicate child'; END IF; child:=r.payload;
   WHEN 'stop' THEN IF stopped IS NOT NULL THEN RAISE EXCEPTION 'Duplicate stop'; END IF; stopped:=r.payload;
   WHEN 'controller_completion' THEN IF completed IS NOT NULL THEN RAISE EXCEPTION 'Duplicate completion'; END IF; completed:=r.payload;
   ELSE RAISE EXCEPTION 'Foreign controller receipt';
  END CASE;
 END LOOP;
 IF intent IS NULL OR (st.status='stopped') IS DISTINCT FROM (stopped IS NOT NULL)
 OR (st.status='running' AND ident IS NULL)
 OR (st.status='launch_intended' AND receipts<>1)
 OR (ident IS NULL AND (child IS NOT NULL OR stopped IS NOT NULL OR completed IS NOT NULL))
 OR (completed IS NOT NULL AND (child IS NULL OR stopped IS NULL))
 THEN RAISE EXCEPTION 'OE controller receipt prefix'; END IF;
 declaration:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
 'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
 'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
 'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
 'ipc',jsonb_build_object('ipc_policy',a.ipc_policy,'ticket_sha256',a.ticket_sha256));
 admission_sha:=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(declaration,true),'UTF8')),'hex');
 bootstrap:=e.bootstrap_canonical::jsonb;
 SELECT '{"program":'||@SCHEMA@.mineagent_structured_raw_json(sp.config_canonical::json#>'{search,domain,seed_program}')
  ||',"schema_version":"mineagent.oe-structured-candidate.v1"}' INTO STRICT seed
 FROM @SCHEMA@.oe_structured_specs sp WHERE sp.owner_id=e.owner_id AND sp.run_id=e.run_id;
 IF intent->'admission' IS DISTINCT FROM declaration
 OR intent->>'bootstrap_sha256' IS DISTINCT FROM e.bootstrap_sha256
 OR (intent->>'bootstrap_size_bytes')::integer IS DISTINCT FROM octet_length(e.bootstrap_canonical)
 OR intent->>'seed_sha256' IS DISTINCT FROM bootstrap#>>'{spec,seed_candidate_sha256}'
 OR intent->>'seed_sha256' IS DISTINCT FROM encode(sha256(convert_to(seed,'UTF8')),'hex')
 OR (intent->>'seed_size_bytes')::integer IS DISTINCT FROM octet_length(seed)
 OR intent->>'issued_at' IS DISTINCT FROM bootstrap->>'issued_at'
 OR intent->>'not_after' IS DISTINCT FROM bootstrap->>'not_after'
 OR intent->>'issued_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
 OR intent->>'not_after' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
 THEN RAISE EXCEPTION 'OE controller exact bootstrap declaration'; END IF;
 issued:=(intent->>'issued_at')::timestamptz; deadline:=(intent->>'not_after')::timestamptz;
 IF deadline<=issued OR deadline>issued+interval '600 seconds'
 THEN RAISE EXCEPTION 'OE original controller deadline'; END IF;
 inventory:=jsonb_build_object('schema_version','mineagent.oe-structured-bootstrap-inventory.v1','files',jsonb_build_array(
  jsonb_build_object('destination','/bootstrap/bootstrap.json','sha256',intent->'bootstrap_sha256','size_bytes',intent->'bootstrap_size_bytes'),
  jsonb_build_object('destination','/bootstrap/seed.json','sha256',intent->'seed_sha256','size_bytes',intent->'seed_size_bytes')));
 IF a.input_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(inventory,true),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'OE controller input inventory'; END IF;
 SELECT * INTO paid FROM @SCHEMA@.research_budget_settlements WHERE reservation_id=b.id;
 IF b.state='settled' THEN
  IF stopped IS NULL OR paid.id IS NULL
  OR ROW(paid.owner_id,paid.run_id,paid.attempt_id,paid.generation) IS DISTINCT FROM ROW(a.owner_id,a.run_id,a.attempt_id,a.generation)
  OR paid.outcome IS DISTINCT FROM 'conservative_unknown' OR paid.charge IS DISTINCT FROM b.amounts
  THEN RAISE EXCEPTION 'OE controller full stop charge'; END IF;
 ELSIF paid.id IS NOT NULL THEN RAISE EXCEPTION 'OE premature controller charge'; END IF;
 IF ident IS NULL THEN RETURN; END IF;
 launch:=jsonb_build_object('schema_version','mineagent.oe-structured-controller-launch.v1',
 'intent',intent,'runtime_name','mineagent-oe-structured-controller-'||a.id::text,
 'root_device',ident->'root_device','root_inode',ident->'root_inode');
 launch_sha:=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(launch,true),'UTF8')),'hex');
 identity_sha:=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(ident,true),'UTF8')),'hex');
 IF ident->>'admission_sha256' IS DISTINCT FROM admission_sha
 OR ident->>'launch_sha256' IS DISTINCT FROM launch_sha
 OR ident->>'image_id' IS DISTINCT FROM a.image_id
 OR @SCHEMA@.mineagent_rd_controller_kernel(ident->'leader',0) IS NOT TRUE
 THEN RAISE EXCEPTION 'OE controller exact leader and launch'; END IF;
 IF child IS NOT NULL THEN
  child_sha:=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(child,true),'UTF8')),'hex');
  n:=jsonb_array_length(child->'namespace_pids');
  IF child->>'identity_sha256' IS DISTINCT FROM identity_sha
  OR child->>'admission_sha256' IS DISTINCT FROM admission_sha
  OR @SCHEMA@.mineagent_rd_controller_kernel(child->'controller',65533) IS NOT TRUE
  OR child#>'{controller,ppid}' IS DISTINCT FROM ident#>'{leader,host_pid}'
  OR child#>'{controller,host_pid}'=ident#>'{leader,host_pid}'
  OR child#>'{controller,pid_namespace}' IS DISTINCT FROM ident#>'{leader,pid_namespace}'
  OR child#>'{controller,cgroup}' IS DISTINCT FROM ident#>'{leader,cgroup}'
  OR child->'namespace_pids'->0 IS DISTINCT FROM child#>'{controller,host_pid}'
  OR child->'namespace_pids'->(n-1) IS DISTINCT FROM child->'namespace_pid'
  OR child->'peer_pid' IS DISTINCT FROM child#>'{controller,host_pid}'
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(child->'namespace_pids') x WHERE (x.value::text)::numeric<=0)
  THEN RAISE EXCEPTION 'OE controller exact kernel peer'; END IF;
  started:=@SCHEMA@.mineagent_rd_canonical_json(jsonb_build_object(
   'schema','mineagent.oe-structured-controller-report.v1','event','started',
   'child_namespace_pid',child->'namespace_pid','child_start_ticks',child#>'{controller,start_ticks}'),true)||E'\n';
  IF child->>'started_report_sha256' IS DISTINCT FROM encode(sha256(convert_to(started,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'OE controller original started report'; END IF;
 END IF;
 IF stopped IS NOT NULL THEN
  stop_sha:=encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(stopped,true),'UTF8')),'hex');
  IF stopped->>'launch_sha256' IS DISTINCT FROM launch_sha
  OR stopped->>'identity_sha256' IS DISTINCT FROM identity_sha
  OR stopped->>'container_id' IS DISTINCT FROM ident->>'container_id'
  OR stopped->'child_sha256' IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE to_jsonb(child_sha) END)
  OR stopped->'child_kernel_identity_absent' IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END)
  THEN RAISE EXCEPTION 'OE controller stopped chain'; END IF;
 END IF;
 IF completed IS NOT NULL THEN
  quiescent:=@SCHEMA@.mineagent_rd_canonical_json(jsonb_build_object(
   'schema','mineagent.oe-structured-controller-report.v1','event','quiescent',
   'child_namespace_pid',child->'namespace_pid','child_start_ticks',child#>'{controller,start_ticks}',
   'child_exit_code',0,'descendants_reaped',true),true)||E'\n';
  IF receipts<>5 OR completed->>'launch_sha256' IS DISTINCT FROM launch_sha
  OR completed->>'identity_sha256' IS DISTINCT FROM identity_sha
  OR completed->>'child_sha256' IS DISTINCT FROM child_sha
  OR completed->>'stop_sha256' IS DISTINCT FROM stop_sha
  OR completed->>'started_report' IS DISTINCT FROM started
  OR completed->>'quiescent_report' IS DISTINCT FROM quiescent
  OR completed->>'started_report_sha256' IS DISTINCT FROM child->>'started_report_sha256'
  OR completed->>'quiescent_report_sha256' IS DISTINCT FROM encode(sha256(convert_to(quiescent,'UTF8')),'hex')
  OR completed->'reports_eof' IS DISTINCT FROM 'true'::jsonb
  OR completed->'daemon_exit_code' IS DISTINCT FROM '124'::jsonb
  OR completed->'daemon_oom_killed' IS DISTINCT FROM 'false'::jsonb
  OR NOT ((completed->>'kernel_absent_monotonic_ns')::numeric BETWEEN
    (completed->>'original_deadline_monotonic_ns')::numeric AND
    (completed->>'original_deadline_monotonic_ns')::numeric+1000000000)
  THEN RAISE EXCEPTION 'OE positive original completion differs'; END IF;
 END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_oes_controller_completed(epoch_id uuid) RETURNS boolean
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM @SCHEMA@.mineagent_oes_controller_check(epoch_id);
 RETURN EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_epochs e
 JOIN @SCHEMA@.research_process_admissions a ON a.id=e.controller_admission_id
 JOIN @SCHEMA@.research_process_states st ON st.admission_id=a.id
 JOIN @SCHEMA@.research_budget_reservations b ON b.id=a.reservation_id
 JOIN @SCHEMA@.research_budget_settlements paid ON paid.reservation_id=b.id
 WHERE e.id=epoch_id AND st.status='stopped' AND b.state='settled'
 AND paid.outcome='conservative_unknown' AND paid.charge=b.amounts
 AND EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts r
  WHERE r.admission_id=a.id AND r.kind='controller_completion'));
END $$;
"""
