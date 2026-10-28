"""Closed020 RD-controller SQL proposal; not installed or runtime acceptance.

Historical015 functions are neither replaced nor dropped.018's own controller
profile dispatch must separately be versioned by the migration owner. These
predicates validate persisted metadata, not SO_PEERCRED/proc/Docker observations.
Actual kernel observations, bounded journal reads, backupv5 and hosted acceptance
remain necessary. Fixed receipt-kind sequence uses BEFORE INSERT existence checks,
not caller-created timestamp/UUID ordering or fabricated ordinal columns.
"""

import ast
import hashlib
import json
import re
from pathlib import Path

from backend.app.rdagent_persistence_ddl import CONTROLLER_PROFILE_GATE
from backend.app.rdagent_receipt_schema_sql import (
    receipt_contract_schema,
    receipt_schema_sql,
)
from runtimes.rdagent_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerStop,
)

JOURNAL_SHA256 = "0f76db0bb4ae05ad481b582c090d3ca18b78e4ff6a620948b0243aa66cc47fdd"
TABLES = (
    "research_process_admissions",
    "research_process_states",
    "research_process_receipts",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_budget_controls",
    "research_runs",
    "factor_research_specs",
    "factor_research_attempt_bindings",
    "factor_controller_launches",
)
KINDS = ("launch_intent", "launch_identity", "controller_child", "stop")


def _schema(cls):
    schema = cls.model_json_schema()

    def normalize(node):
        if isinstance(node, dict):
            if "exclusiveMinimum" in node:
                node["minimum"] = node.pop("exclusiveMinimum") + 1
            if "prefixItems" in node:
                items = node.pop("prefixItems")
                if any(item != items[0] for item in items):
                    raise ValueError("Nonuniform tuple requires a new SQL grammar")
                node["items"] = items[0]
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    normalize(schema)
    return schema


def render_sql(schema="public"):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact schema required")
    # 020 is historical.027 changes the controller deadline without changing
    # receipt shape. Pin the frozen020 grammar and compare current structures;
    # do not pretend the evolved runtime still has the old whole-source hash.
    historical = Path(__file__).parents[1] / "alembic/versions/020_rd_controller.py"
    module = ast.parse(historical.read_text())
    frozen = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if (
        hashlib.sha256(frozen.encode()).hexdigest()
        != "0356ba7a550b5432fdd9dc675f1b62bf7672b546c1196dfdbc68a9d855a0d069"
    ):
        raise ValueError("Historical020 controller grammar changed")
    choices = []
    for kind, cls in zip(
        KINDS,
        (ControllerIntent, ControllerIdentity, ControllerChild, ControllerStop),
        strict=True,
    ):
        raw = json.dumps(
            receipt_contract_schema(cls, "controller"),
            sort_keys=True,
            separators=(",", ":"),
        ).replace("'", "''")
        clause = f"WHEN '{kind}' THEN rule := '{raw}'::jsonb;"
        if clause not in frozen:
            raise ValueError("Current receipt shape differs from frozen020 grammar")
        choices.append(clause)
    alterations = f"""ALTER TABLE research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK (({CONTROLLER_PROFILE_GATE}) OR
 (schema_version='mineagent.process-admission.v2' AND role='rd_orchestrator'
 AND profile='mineagent.rdagent-orchestrator.v2' AND ipc_policy='host_ticket'
 AND ticket_sha256 IS NOT NULL AND ticket_sha256 ~ '^[a-f0-9]{{64}}$'));
ALTER TABLE research_process_receipts DROP CONSTRAINT ck_research_process_receipt_kind,
 ADD CONSTRAINT ck_research_process_receipt_kind CHECK (kind IN ('launch_intent','launch_identity','controller_child','stop','extraction'));
"""
    grammar = r"""
CREATE FUNCTION @S@.mineagent_rd_controller_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@S@,pg_catalog AS $$
DECLARE rule jsonb;
BEGIN
 CASE kind
 @RULES@
 ELSE RETURN false;
 END CASE;
 RETURN @S@.mineagent_rd_controller_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @S@.mineagent_rd_controller_kernel(v jsonb, expected_uid integer) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=@S@,pg_catalog AS $$
 SELECT coalesce((v->>'host_pid')::numeric>0 AND (v->>'start_ticks')::numeric>0
 AND (v->>'ppid')::numeric>=0 AND v->>'pid_namespace' ~ '^pid:\[[0-9]+\]$'
 AND length(v->>'cgroup') BETWEEN 1 AND 4096
 AND v->'uids'=to_jsonb(ARRAY[expected_uid,expected_uid,expected_uid,expected_uid])
 AND v->'gids'=to_jsonb(ARRAY[expected_uid,expected_uid,expected_uid,expected_uid]),false)
$$;
CREATE FUNCTION @S@.mineagent_rd_controller_receipt_order() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE a record;
BEGIN
 SELECT * INTO a FROM research_process_admissions WHERE id=NEW.admission_id;
 IF NEW.kind='controller_child' AND (a.profile IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v2'
  OR a.role IS DISTINCT FROM 'rd_orchestrator') THEN RAISE EXCEPTION 'Child receipt belongs only to RD protected controller'; END IF;
 IF a.profile IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v2' THEN RETURN NEW; END IF;
 -- Parent run lock is acquired by existing018 BEFORE trigger. It serializes all
 -- controller receipt writers; no advisory acquisition or waiting is added here.
 IF NEW.kind NOT IN ('launch_intent','launch_identity','controller_child','stop')
 THEN RAISE EXCEPTION 'RD controller cannot publish numeric extraction'; END IF;
 IF EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='stop')
 THEN RAISE EXCEPTION 'Stopped controller receipt chain is closed'; END IF;
 IF NEW.kind<>'launch_intent' AND NOT EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_intent')
 THEN RAISE EXCEPTION 'Controller intent must precede physical identity'; END IF;
 IF NEW.kind IN ('controller_child','stop') AND NOT EXISTS(SELECT 1 FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_identity')
 THEN RAISE EXCEPTION 'Protected leader identity must precede child or stop'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_controller_receipt_order BEFORE INSERT ON research_process_receipts
 FOR EACH ROW EXECUTE FUNCTION @S@.mineagent_rd_controller_receipt_order();

CREATE FUNCTION @S@.mineagent_rd_controller_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@S@,pg_catalog AS $$
DECLARE owner_uuid uuid; a record; rr record; br record; bs record; ps record;
 sp record; ab record; cl record; p record; intent jsonb; ident jsonb; child jsonb; stopped jsonb;
 decl jsonb; launch jsonb; expected_bootstrap jsonb; report jsonb; n integer;
 intent_hash text; identity_hash text; child_hash text; launch_hash text;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM research_process_receipts child_receipt JOIN research_process_admissions child_admission ON child_admission.id=child_receipt.admission_id
 WHERE child_receipt.owner_id=owner_uuid AND child_receipt.kind='controller_child' AND
  (child_admission.profile<>'mineagent.rdagent-orchestrator.v2' OR child_admission.role<>'rd_orchestrator'))
 THEN RAISE EXCEPTION 'RD child cannot extend historical process grammar'; END IF;
 FOR a IN SELECT * FROM research_process_admissions WHERE owner_id=owner_uuid AND profile='mineagent.rdagent-orchestrator.v2' LOOP
  IF (a.schema_version,a.role,a.ipc_policy) IS DISTINCT FROM
    ('mineagent.process-admission.v2'::varchar,'rd_orchestrator'::varchar,'host_ticket'::varchar)
    OR a.ticket_sha256 IS NULL OR a.ticket_sha256 !~ '^[a-f0-9]{64}$'
  THEN RAISE EXCEPTION 'Exact RD controller declaration required'; END IF;
  SELECT * INTO rr FROM research_runs WHERE id=a.run_id AND owner_id=a.owner_id;
  IF NOT FOUND OR rr.engine<>'rdagent' OR rr.purpose<>'factor_research'
  THEN RAISE EXCEPTION 'RD controller requires genuine RD run'; END IF;
  SELECT * INTO sp FROM factor_research_specs WHERE run_id=a.run_id AND owner_id=a.owner_id;
  IF NOT FOUND OR sp.config<>rr.config OR sp.config_sha256<>rr.request_sha256
  THEN RAISE EXCEPTION 'Controller immutable spec missing'; END IF;
  SELECT * INTO ab FROM factor_research_attempt_bindings WHERE id=a.logical_operation_id
   AND (owner_id,run_id,attempt_id,generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation);
  IF NOT FOUND OR ab.spec_id<>sp.id OR ab.resumed_checkpoint_id IS NOT NULL
  THEN RAISE EXCEPTION 'Controller attempt binding differs'; END IF;
  SELECT * INTO cl FROM factor_controller_launches WHERE admission_id=a.id
   AND (owner_id,run_id,attempt_id,generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation);
  IF NOT FOUND OR cl.attempt_binding_id<>ab.id THEN RAISE EXCEPTION 'Controller launch domain binding missing'; END IF;
  expected_bootstrap:=jsonb_build_object('schema_version','mineagent.rdagent-operational-bootstrap.v1',
   'owner_id',a.owner_id,'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,
   'spec_id',sp.id,'attempt_binding_id',ab.id,'config',sp.config,'config_sha256',sp.config_sha256,
   'input_sha256',sp.input_sha256,'continuation','fresh_only','input_layout','single_bootstrap_json_no_other_mounts');
  IF cl.bootstrap<>expected_bootstrap OR cl.bootstrap_sha256<>a.input_sha256
   OR a.input_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(expected_bootstrap,true),'UTF8')),'hex')
   OR a.image_id<>sp.config->'runtime'->>'orchestrator_image'
  THEN RAISE EXCEPTION 'Controller bootstrap/image commitment differs'; END IF;
  SELECT * INTO br FROM research_budget_reservations WHERE id=a.reservation_id;
  IF NOT FOUND OR (br.owner_id,br.run_id,br.attempt_id,br.generation) IS DISTINCT FROM
    (a.owner_id,a.run_id,a.attempt_id,a.generation) OR br.purpose<>'process_unit'
    OR (br.amounts->>'process_slots')::numeric<>1 OR (br.amounts->>'wall_milliseconds')::numeric<60000
    OR (br.amounts->>'storage_bytes')::numeric<67174400
    OR br.amounts->>'calls'<>'0' OR br.amounts->>'tokens'<>'0'
    OR br.amounts->>'mutation_slots'<>'0' OR br.amounts->>'evaluator_executions'<>'0'
  THEN RAISE EXCEPTION 'Controller physical reservation floor/scope differs'; END IF;
  SELECT * INTO ps FROM research_process_states WHERE admission_id=a.id;
  IF NOT FOUND OR (ps.owner_id,ps.run_id,ps.attempt_id,ps.generation) IS DISTINCT FROM
    (a.owner_id,a.run_id,a.attempt_id,a.generation) OR ps.status NOT IN ('launch_intended','running','uncertain','stopped')
  THEN RAISE EXCEPTION 'Controller projection differs'; END IF;
  intent:=NULL;ident:=NULL;child:=NULL;stopped:=NULL;
  FOR p IN SELECT * FROM research_process_receipts WHERE admission_id=a.id LOOP
   IF (p.owner_id,p.run_id,p.attempt_id,p.generation) IS DISTINCT FROM
      (a.owner_id,a.run_id,a.attempt_id,a.generation)
    OR @S@.mineagent_rd_controller_payload(p.kind,p.payload) IS NOT TRUE
    OR p.payload_sha256<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(p.payload,true),'UTF8')),'hex')
   THEN RAISE EXCEPTION 'Exact controller receipt grammar/hash required'; END IF;
   CASE p.kind WHEN 'launch_intent' THEN intent:=p.payload;
    WHEN 'launch_identity' THEN ident:=p.payload;
    WHEN 'controller_child' THEN child:=p.payload;
    WHEN 'stop' THEN stopped:=p.payload;
    ELSE RAISE EXCEPTION 'Unexpected controller receipt'; END CASE;
  END LOOP;
  IF intent IS NULL THEN RAISE EXCEPTION 'Controller launch intent required'; END IF;
  decl:=jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
   'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
   'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,'profile',a.profile,
   'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
   'ipc',jsonb_build_object('ipc_policy','host_ticket','ticket_sha256',a.ticket_sha256));
  IF intent->'admission'<>decl OR intent->>'bootstrap_sha256'<>a.input_sha256
   OR (intent->>'bootstrap_size_bytes')::integer<>octet_length(convert_to(@S@.mineagent_rd_canonical_json(expected_bootstrap,true),'UTF8'))
   OR (intent->>'not_after')::timestamptz <= (intent->>'issued_at')::timestamptz
   OR (intent->>'not_after')::timestamptz-(intent->>'issued_at')::timestamptz>interval '60 seconds'
  THEN RAISE EXCEPTION 'Controller immutable intent differs'; END IF;
  IF ps.status IN ('running','stopped') AND ident IS NULL THEN RAISE EXCEPTION 'Controller leader required'; END IF;
  IF (stopped IS NOT NULL) IS DISTINCT FROM (ps.status='stopped') THEN RAISE EXCEPTION 'Controller stopped projection differs'; END IF;
  IF ident IS NULL AND (child IS NOT NULL OR stopped IS NOT NULL) THEN RAISE EXCEPTION 'Controller leader predecessor missing'; END IF;
  IF ident IS NOT NULL THEN
   launch:=jsonb_build_object('schema_version','mineagent.rd-controller-launch.v1','intent',intent,
    'runtime_name','mineagent-rd-controller-'||a.id::text,'root_device',ident->'root_device','root_inode',ident->'root_inode');
   launch_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(launch,true),'UTF8')),'hex');
   identity_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(ident,true),'UTF8')),'hex');
   IF ident->>'launch_sha256'<>launch_hash OR ident->>'image_id'<>a.image_id
    OR ident->>'admission_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(decl,true),'UTF8')),'hex')
    OR @S@.mineagent_rd_controller_kernel(ident->'leader',0) IS NOT TRUE
   THEN RAISE EXCEPTION 'Protected leader physical identity differs'; END IF;
  END IF;
  child_hash:=NULL;
  IF child IS NOT NULL THEN
   n:=jsonb_array_length(child->'namespace_pids');
   IF child->>'identity_sha256'<>identity_hash OR child->>'admission_sha256'<>ident->>'admission_sha256'
    OR @S@.mineagent_rd_controller_kernel(child->'controller',65533) IS NOT TRUE
    OR child->'controller'->'ppid'<>ident->'leader'->'host_pid'
    OR child->'controller'->'host_pid'=ident->'leader'->'host_pid'
    OR child->'controller'->'pid_namespace'<>ident->'leader'->'pid_namespace'
    OR child->'controller'->'cgroup'<>ident->'leader'->'cgroup'
    OR child->'peer_pid'<>child->'controller'->'host_pid'
    OR child->'namespace_pids'->0<>child->'controller'->'host_pid'
    OR child->'namespace_pids'->(n-1)<>child->'namespace_pid'
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(child->'namespace_pids') x WHERE (x.value::text)::numeric<=0)
   THEN RAISE EXCEPTION 'Exact trusted child/peer/NSpid identity differs'; END IF;
   report:=jsonb_build_object('schema','mineagent.rdagent-controller-report.v1','event','started',
    'child_namespace_pid',child->'namespace_pid','child_start_ticks',child->'controller'->'start_ticks');
   IF child->>'started_report_sha256'<>encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(report,true)||E'\n','UTF8')),'hex')
   THEN RAISE EXCEPTION 'Trusted startup frame commitment differs'; END IF;
   child_hash:=encode(sha256(convert_to(@S@.mineagent_rd_canonical_json(child,true),'UTF8')),'hex');
  END IF;
  IF stopped IS NOT NULL AND (stopped->>'launch_sha256'<>launch_hash
    OR stopped->>'identity_sha256'<>identity_hash OR stopped->>'container_id'<>ident->>'container_id'
    OR (stopped->>'child_sha256') IS DISTINCT FROM child_hash
    OR (stopped->'child_kernel_identity_absent') IS DISTINCT FROM CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END)
  THEN RAISE EXCEPTION 'Controller positive stop metadata differs'; END IF;
  SELECT * INTO bs FROM research_budget_settlements WHERE reservation_id=br.id;
  IF br.state='settled' THEN
   IF NOT FOUND OR stopped IS NULL OR bs.outcome<>'conservative_unknown'
    OR bs.charge IS DISTINCT FROM br.amounts
    OR (bs.owner_id,bs.run_id,bs.attempt_id,bs.generation) IS DISTINCT FROM
      (a.owner_id,a.run_id,a.attempt_id,a.generation)
   THEN RAISE EXCEPTION 'Controller settlement requires stopped full conservative charge'; END IF;
  ELSIF FOUND OR br.state NOT IN ('reserved','uncertain') THEN RAISE EXCEPTION 'Controller unresolved budget must remain held'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""
    triggers = "\n".join(
        f"CREATE CONSTRAINT TRIGGER rd_controller_closure AFTER INSERT OR UPDATE OR DELETE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @S@.mineagent_rd_controller_consistent();"
        for table in TABLES
    )
    return (
        receipt_schema_sql("controller").replace("@SCHEMA@", "@S@")
        + alterations
        + grammar.replace("@RULES@", "\n".join(choices))
        + triggers
    ).replace("@S@", '"' + schema + '"')
