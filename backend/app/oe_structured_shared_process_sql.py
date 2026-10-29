"""Narrow027 process dispatch additions to pinned installed024/025/026 predicates.

026 explicitly replaces the controller bootstrap grammar. Exact reviewed bodies are
loaded from frozen migration literals, not old018/live generator approximations.
The assembler owns new CHECK/profile activation, locking, reverse triggers and
downgrade refusal when027 evidence remains. No old process grammar is weakened.
"""

import ast
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "alembic" / "versions"
PINS = {
    "mineagent_rd_controller_receipt_order": (
        "027c",
        "153a7dba8bd98a5324fee8c336666f368227cd4997b8e6f2358d59e33fd6bfbd",
    ),
    "mineagent_rd_controller_consistent": (
        "027c",
        "d127f1202eebe82afe8c00b57bfb1b05da0634bbaf5f720028f587342ec25b60",
    ),
    "mineagent_structured_consistent": (
        "025",
        "bfb23deb0726d3fd516c251abd28f0010aaa20ca8246d54a56bd102315b62c85",
    ),
}


def frozen_function(name):
    revision, expected = PINS[name]
    paths = tuple(ROOT.glob(revision + "_*.py"))
    if len(paths) != 1:
        raise ValueError("Unique frozen process migration required")
    values = [
        node.value.value
        for node in ast.parse(paths[0].read_text()).body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(
            isinstance(target, ast.Name) and target.id == "UPGRADE_SQL"
            for target in node.targets
        )
    ]
    if len(values) != 1:
        raise ValueError("Exact frozen UPGRADE_SQL required")
    sql = values[0]
    matches = tuple(
        re.finditer(
            r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?"
            + re.escape(name)
            + r"\(",
            sql,
        )
    )
    if len(matches) != 1:
        raise ValueError("Unique inherited process function required")
    start = matches[0].start()
    raw = sql[start : sql.index("END $$;", start) + len("END $$;")]
    if hashlib.sha256(raw.encode()).hexdigest() != expected:
        raise ValueError("Reviewed inherited process function changed")
    return raw


def _once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("Exact inherited process splice required")
    return value.replace(old, new, 1)


def _replaceable(value):
    return re.sub(
        r"^CREATE (?:OR REPLACE )?FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        value,
        count=1,
    )


OLD_CHILD_BRANCH = """OR (child_admission.profile='mineagent.openevolve-orchestrator.v1' AND child_admission.role='openevolve_orchestrator'
           AND child_admission.schema_version='mineagent.process-admission.v2' AND child_admission.ipc_policy='host_ticket'
           AND child_admission.ticket_sha256 ~ '^[a-f0-9]{64}$')"""
NEW_CHILD_BRANCH = (
    OLD_CHILD_BRANCH
    + """
       OR (child_admission.profile='mineagent.oe-structured-orchestrator.v1' AND child_admission.role='openevolve_orchestrator'
           AND child_admission.schema_version='mineagent.process-admission.v2' AND child_admission.ipc_policy='host_ticket'
           AND child_admission.ticket_sha256 ~ '^[a-f0-9]{64}$' AND child_admission.generation=1 AND child_admission.launch_ordinal=1
           AND EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_epochs oe
             JOIN @SCHEMA@.oe_structured_specs os ON os.owner_id=oe.owner_id AND os.run_id=oe.run_id
             JOIN @SCHEMA@.research_runs normal_run ON normal_run.id=os.run_id AND normal_run.owner_id=os.owner_id
             WHERE oe.controller_admission_id=child_admission.id AND oe.id=child_admission.logical_operation_id
              AND (oe.owner_id,oe.run_id,oe.attempt_id,oe.generation)=(child_admission.owner_id,child_admission.run_id,child_admission.attempt_id,child_admission.generation)
              AND (normal_run.purpose,normal_run.engine)=('strategy_search','openevolve')
              AND os.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'))"""
)

ORDER_ANCHOR = " SELECT * INTO STRICT a FROM research_process_admissions WHERE id=NEW.admission_id;"
ORDER_EXTENSION = r"""
 -- New completion kind never becomes an extension point for historical roles.
 IF NEW.kind='controller_completion' AND a.profile IS DISTINCT FROM 'mineagent.oe-structured-orchestrator.v1'
 THEN RAISE EXCEPTION 'Completion requires exact027 protected controller'; END IF;
 IF a.profile='mineagent.oe-structured-orchestrator.v1' THEN
  IF (a.schema_version,a.role,a.ipc_policy,a.generation,a.launch_ordinal) IS DISTINCT FROM
   ('mineagent.process-admission.v2','openevolve_orchestrator','host_ticket',1,1)
  OR a.ticket_sha256 IS NULL OR a.ticket_sha256 !~ '^[a-f0-9]{64}$'
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_epochs oe
   JOIN @SCHEMA@.oe_structured_specs os ON os.owner_id=oe.owner_id AND os.run_id=oe.run_id
   JOIN @SCHEMA@.research_runs rr ON rr.owner_id=os.owner_id AND rr.id=os.run_id
   WHERE oe.controller_admission_id=a.id AND oe.id=a.logical_operation_id
    AND (oe.owner_id,oe.run_id,oe.attempt_id,oe.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
    AND (rr.purpose,rr.engine)=('strategy_search','openevolve')
    AND os.config->>'schema_version'='mineagent.oe-structured-provider-run.v1')
  THEN RAISE EXCEPTION 'Exact027 controller consumer required'; END IF;
  IF @SCHEMA@.mineagent_oes_controller_payload(NEW.kind,NEW.payload) IS NOT TRUE
  OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(NEW.payload,true),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'Exact027 controller receipt required'; END IF;
  IF EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='controller_completion')
  THEN RAISE EXCEPTION 'Completed027 controller chain immutable'; END IF;
  IF EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='stop')
     AND NEW.kind<>'controller_completion'
  THEN RAISE EXCEPTION 'Stopped027 controller accepts only original completion'; END IF;
  IF NEW.kind='launch_intent' AND EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id)
  THEN RAISE EXCEPTION 'Controller intent must be first'; END IF;
  IF NEW.kind<>'launch_intent' AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='launch_intent')
  THEN RAISE EXCEPTION 'Controller intent must precede physical receipt'; END IF;
  IF NEW.kind IN ('controller_child','stop','controller_completion') AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='launch_identity')
  THEN RAISE EXCEPTION 'Controller identity must precede child or stop'; END IF;
  IF NEW.kind='controller_completion' AND
   (NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='controller_child')
    OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts p WHERE p.admission_id=a.id AND p.kind='stop')
    OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_states ps WHERE ps.admission_id=a.id AND ps.status='stopped'))
  THEN RAISE EXCEPTION 'Completion requires original child and whole stop'; END IF;
  RETURN NEW;
 END IF;
"""

OLD_NATIVE_GATE = """AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.structured_strategy_specs WHERE run_id=(value->>'run_id')::uuid AND owner_id=(value->>'owner_id')::uuid)"""
NATIVE_CONSUMER = r"""EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_executions oe
 JOIN @SCHEMA@.oe_structured_specs os ON os.run_id=oe.run_id AND os.owner_id=oe.owner_id
 JOIN @SCHEMA@.research_runs rr ON rr.id=os.run_id AND rr.owner_id=os.owner_id
 WHERE (oe.owner_id::text,oe.run_id::text,oe.attempt_id::text,oe.generation::text,oe.id::text)=
  (value->>'owner_id',value->>'run_id',value->>'attempt_id',value->>'generation',value->>'logical_operation_id')
 AND (rr.purpose,rr.engine)=('strategy_search','openevolve')
 AND os.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'
 AND (value->>'schema_version',value->>'role',value->>'ipc_policy',value->>'launch_ordinal')=
  ('mineagent.process-admission.v2','native_evaluator','none','1')
 AND value->>'ticket_sha256' IS NULL)"""
NATIVE_CHECK_ANCHOR = (
    " IF TG_TABLE_NAME IN ('research_model_operations','research_operation_contexts')"
)
NATIVE_CHECK = (
    """ IF TG_TABLE_NAME='research_process_admissions' AND value->>'profile'='mineagent.factor-strategy-engine.v1'
 AND """
    + NATIVE_CONSUMER
    + """ THEN
  PERFORM @SCHEMA@.mineagent_oes_native_check((value->>'id')::uuid);
 END IF;
"""
)


def render_upgrade_sql():
    order = _once(
        frozen_function("mineagent_rd_controller_receipt_order"),
        ORDER_ANCHOR,
        ORDER_ANCHOR + ORDER_EXTENSION,
    )
    controller = _once(
        frozen_function("mineagent_rd_controller_consistent"),
        OLD_CHILD_BRANCH,
        NEW_CHILD_BRANCH,
    )
    native = _once(
        frozen_function("mineagent_structured_consistent"),
        OLD_NATIVE_GATE,
        OLD_NATIVE_GATE + "\n AND NOT " + NATIVE_CONSUMER,
    )
    native = _once(native, NATIVE_CHECK_ANCHOR, NATIVE_CHECK + NATIVE_CHECK_ANCHOR)
    return "\n".join(_replaceable(value) for value in (order, controller, native))


def render_downgrade_sql():
    # The central assembler must first reject retained027 evidence. This only
    # restores exact old function bodies; it does not discard evidence or tables.
    return "\n".join(_replaceable(frozen_function(name)) for name in PINS)
