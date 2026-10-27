"""Offline024 exact process journal predicates, frozen by migration assembly.

Persistence agreement cannot prove a kernel observation. Actual protected host
reads and required Docker acceptance remain independent mandatory boundaries.
"""

import json
import re

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.openevolve_identity_sql import _expanded
from runtimes.openevolve_evaluator import journal as native
from runtimes.openevolve_operational import process_journal as controller
from runtimes.openevolve_operational.profile import STORAGE_FLOOR


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact SQL schema required")
    cases = []
    for profile, models in (
        (
            controller.PROFILE,
            {
                "launch_intent": controller.ControllerIntent,
                "launch_identity": controller.ControllerIdentity,
                "controller_child": controller.ControllerChild,
                "stop": controller.ControllerStop,
            },
        ),
        (
            native.PROFILE,
            {
                "launch_intent": native.NativeIntent,
                "launch_identity": native.NativeIdentity,
                "extraction": native.NativeExtraction,
                "stop": native.NativeStop,
            },
        ),
    ):
        for kind, model in models.items():
            rule = _expanded(model)
            _require_serialized_keys(rule)
            raw = json.dumps(rule, sort_keys=True, separators=(",", ":")).replace(
                "'", "''"
            )
            cases.append(
                f"WHEN profile='{profile}' AND kind='{kind}' THEN rule:='{raw}'::jsonb;"
            )
    triggers = "\n".join(
        f"CREATE CONSTRAINT TRIGGER oe024_process_consistent AFTER INSERT OR UPDATE OR DELETE ON {schema}.{name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_oe_process_consistent();"
        for name in (
            "research_process_admissions",
            "research_process_receipts",
            "research_process_states",
            "research_budget_reservations",
            "research_budget_settlements",
            "search_controller_launches",
            "search_evaluations",
            "search_evaluation_inputs",
            "search_evaluation_preparations",
        )
    )
    return (
        (
            SQL.replace("@S@", schema)
            .replace("@RULES@", "\n".join(cases))
            .replace("@NATIVE@", native.PROFILE)
            .replace("@FLOOR@", str(STORAGE_FLOOR))
        )
        + "\n"
        + triggers
    )


SQL = r"""
CREATE FUNCTION @S@.mineagent_oe_process_payload(profile text,kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@S@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @S@.mineagent_oe_shape(v,rule,0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_process_valid(aid uuid,require_stop boolean) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=@S@,pg_catalog AS $$
DECLARE a record; p record; b record; cl record; st record; n integer:=0;
 intent jsonb; ident jsonb; child jsonb; stopped jsonb; extracted jsonb;
 decl jsonb; launch jsonb; inventory jsonb; report jsonb; lh text; ih text;
 controller boolean; issued timestamptz; deadline timestamptz; max_seconds integer;
BEGIN
 SELECT * INTO a FROM research_process_admissions WHERE id=aid;
 IF NOT FOUND OR a.schema_version<>'mineagent.process-admission.v2'
 OR a.profile NOT IN ('mineagent.openevolve-orchestrator.v1','@NATIVE@') THEN RETURN false; END IF;
 controller:=a.profile='mineagent.openevolve-orchestrator.v1';
 IF (controller AND (a.role<>'openevolve_orchestrator' OR a.ipc_policy<>'host_ticket' OR a.ticket_sha256 IS NULL))
 OR (NOT controller AND (a.role<>'native_evaluator' OR a.ipc_policy<>'none' OR a.ticket_sha256 IS NOT NULL)) THEN RETURN false; END IF;
 SELECT * INTO b FROM research_budget_reservations WHERE id=a.reservation_id;
 IF NOT FOUND OR (b.owner_id,b.run_id,b.attempt_id,b.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR b.purpose<>'process_unit' OR (b.amounts->>'process_slots')::numeric<1 THEN RETURN false; END IF;
 IF controller THEN
  IF b.logical_key<>'oe-controller:'||a.logical_operation_id::text
  OR (b.amounts->>'wall_milliseconds')::numeric<600000 OR (b.amounts->>'storage_bytes')::numeric<@FLOOR@ THEN RETURN false; END IF;
 ELSE
  IF b.logical_key<>'oe-native:'||a.logical_operation_id::text||':'||a.launch_ordinal::text
  OR (b.amounts->>'wall_milliseconds')::numeric<90000 OR (b.amounts->>'storage_bytes')::numeric<268500992
  OR (b.amounts->>'evaluator_executions')::numeric<1 THEN RETURN false; END IF;
 END IF;
 FOR p IN SELECT * FROM research_process_receipts WHERE admission_id=aid LOOP
  n:=n+1;
  IF (p.owner_id,p.run_id,p.attempt_id,p.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
  OR @S@.mineagent_oe_process_payload(a.profile,p.kind,p.payload) IS NOT TRUE
  OR p.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(p.payload,true),'UTF8')),'hex') THEN RETURN false; END IF;
  CASE p.kind
   WHEN 'launch_intent' THEN IF intent IS NOT NULL THEN RETURN false; END IF; intent:=p.payload;
   WHEN 'launch_identity' THEN IF ident IS NOT NULL THEN RETURN false; END IF; ident:=p.payload;
   WHEN 'controller_child' THEN IF child IS NOT NULL THEN RETURN false; END IF; child:=p.payload;
   WHEN 'stop' THEN IF stopped IS NOT NULL THEN RETURN false; END IF; stopped:=p.payload;
   WHEN 'extraction' THEN IF extracted IS NOT NULL THEN RETURN false; END IF; extracted:=p.payload;
   ELSE RETURN false;
  END CASE;
 END LOOP;
 IF intent IS NULL OR n NOT BETWEEN 1 AND 4 THEN RETURN false; END IF;
 decl:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
 'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
 'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
 'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
 'ipc',jsonb_build_object('ipc_policy',a.ipc_policy,'ticket_sha256',a.ticket_sha256));
 IF intent->'admission' IS DISTINCT FROM decl
 OR intent->>'issued_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
 OR intent->>'not_after' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$' THEN RETURN false; END IF;
 issued:=(intent->>'issued_at')::timestamptz; deadline:=(intent->>'not_after')::timestamptz;
 max_seconds:=(CASE WHEN controller THEN 600 ELSE 60 END);
 IF deadline<=issued OR deadline>issued+make_interval(secs=>max_seconds) THEN RETURN false; END IF;
 IF controller THEN
  SELECT * INTO cl FROM search_controller_launches WHERE id=a.logical_operation_id;
  IF NOT FOUND OR (cl.owner_id,cl.run_id,cl.attempt_id,cl.generation,cl.admission_id)
   IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation,a.id)
   OR cl.input_inventory_sha256<>a.input_sha256 OR cl.bootstrap_sha256<>intent->>'bootstrap_sha256'
   OR octet_length(cl.bootstrap_canonical)<>(intent->>'bootstrap_size_bytes')::integer
   OR cl.bootstrap->>'issued_at'<>intent->>'issued_at' OR cl.bootstrap->>'not_after'<>intent->>'not_after' THEN RETURN false; END IF;
  inventory:=jsonb_build_object('schema_version','mineagent.openevolve-bootstrap-inventory.v1','files',jsonb_build_array(
   jsonb_build_object('destination','/bootstrap/bootstrap.json','sha256',intent->'bootstrap_sha256','size_bytes',intent->'bootstrap_size_bytes'),
   jsonb_build_object('destination','/bootstrap/seed.py','sha256',intent->'seed_sha256','size_bytes',intent->'seed_size_bytes')));
  IF a.input_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(inventory,true),'UTF8')),'hex') THEN RETURN false; END IF;
 ELSE
  IF intent->>'id'<>a.id::text OR intent->'inputs'->>'evaluation_id'<>a.logical_operation_id::text
   OR a.input_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(intent->'inputs',true),'UTF8')),'hex') THEN RETURN false; END IF;
 END IF;
 SELECT * INTO st FROM research_process_states WHERE admission_id=a.id;
 IF NOT FOUND OR (st.owner_id,st.run_id,st.attempt_id,st.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR st.status NOT IN ('launch_intended','running','uncertain','stopped')
 OR (st.status='stopped') IS DISTINCT FROM (stopped IS NOT NULL)
 OR (st.status='running' AND ident IS NULL)
 OR (st.status='launch_intended' AND (ident IS NOT NULL OR child IS NOT NULL OR stopped IS NOT NULL OR extracted IS NOT NULL))
 OR (b.state='settled' AND stopped IS NULL) THEN RETURN false; END IF;
 IF ident IS NULL THEN RETURN child IS NULL AND stopped IS NULL AND extracted IS NULL AND NOT require_stop; END IF;
 launch:=jsonb_build_object('schema_version',(CASE WHEN controller THEN 'mineagent.oe-controller-launch.v1' ELSE 'mineagent.openevolve-native-process-launch.v1' END),
 'intent',intent,'runtime_name',(CASE WHEN controller THEN 'mineagent-oe-controller-' ELSE 'mineagent-oe-native-' END)||a.id::text,
 'root_device',ident->'root_device','root_inode',ident->'root_inode');
 lh:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(launch,true),'UTF8')),'hex');
 ih:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(ident,true),'UTF8')),'hex');
 IF ident->>'launch_sha256'<>lh OR ident->>'image_id'<>a.image_id
 OR ident->>'admission_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(decl,true),'UTF8')),'hex') THEN RETURN false; END IF;
 IF controller AND @S@.mineagent_rd_controller_kernel(ident->'leader',0) IS NOT TRUE THEN RETURN false; END IF;
 IF child IS NOT NULL THEN
  n:=jsonb_array_length(child->'namespace_pids');
  IF NOT controller OR child->>'identity_sha256'<>ih OR child->>'admission_sha256'<>ident->>'admission_sha256'
  OR @S@.mineagent_rd_controller_kernel(child->'controller',65533) IS NOT TRUE
  OR child->'controller'->'ppid'<>ident->'leader'->'host_pid'
  OR child->'controller'->'host_pid'=ident->'leader'->'host_pid'
  OR child->'controller'->'pid_namespace'<>ident->'leader'->'pid_namespace'
  OR child->'controller'->'cgroup'<>ident->'leader'->'cgroup'
  OR child->'namespace_pids'->0<>child->'controller'->'host_pid'
  OR child->'namespace_pids'->(n-1)<>child->'namespace_pid'
  OR child->'peer_pid'<>child->'controller'->'host_pid'
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(child->'namespace_pids') x WHERE (x.value::text)::numeric<=0) THEN RETURN false; END IF;
  report:=jsonb_build_object('schema','mineagent.openevolve-controller-report.v1','event','started',
   'child_namespace_pid',child->'namespace_pid','child_start_ticks',child->'controller'->'start_ticks');
  IF child->>'started_report_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(report,true)||E'\n','UTF8')),'hex') THEN RETURN false; END IF;
 END IF;
 IF stopped IS NULL THEN RETURN extracted IS NULL AND NOT require_stop; END IF;
 IF stopped->>'launch_sha256'<>lh OR stopped->>'identity_sha256'<>ih OR stopped->>'container_id'<>ident->>'container_id' THEN RETURN false; END IF;
 IF controller THEN
  IF stopped->'child_sha256' IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE to_jsonb(encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(child,true),'UTF8')),'hex')) END)
  OR stopped->'child_kernel_identity_absent' IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END) THEN RETURN false; END IF;
 ELSE
  IF stopped->>'observed_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'
  OR (stopped->'deadline_bound_met'='true'::jsonb AND NOT ((stopped->>'observed_at')::timestamptz BETWEEN issued AND deadline+interval '1 second')) THEN RETURN false; END IF;
  IF extracted IS NOT NULL AND (stopped->'deadline_bound_met'<>'true'::jsonb OR extracted->>'launch_sha256'<>lh OR extracted->>'identity_sha256'<>ih
  OR extracted->>'manifest_sha256'<>intent->'inputs'->>'manifest_sha256'
  OR extracted->>'native_artifacts_sha256'<>intent->'inputs'->>'native_artifacts_sha256') THEN RETURN false; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM research_process_states s WHERE s.admission_id=a.id AND s.status='stopped'
 AND (s.owner_id,s.run_id,s.attempt_id,s.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)) THEN RETURN false; END IF;
 IF b.state='settled' AND NOT EXISTS(SELECT 1 FROM research_budget_settlements z WHERE z.reservation_id=b.id
 AND (z.owner_id,z.run_id,z.attempt_id,z.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
 AND z.outcome='conservative_unknown' AND z.charge=b.amounts) THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;

CREATE FUNCTION @S@.mineagent_oe_process_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE rid uuid; a record;
BEGIN
 rid:=(CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END);
 FOR a IN SELECT id FROM research_process_admissions WHERE run_id=rid
  AND profile IN ('mineagent.openevolve-orchestrator.v1','@NATIVE@') LOOP
  IF @S@.mineagent_oe_process_valid(a.id,false) IS NOT TRUE THEN
   RAISE EXCEPTION 'OE exact protected process graph differs';
  END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""
