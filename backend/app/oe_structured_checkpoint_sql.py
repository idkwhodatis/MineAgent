"""027 ordered checkpoint transfer graph; opaque native files stay reader-verified.

The assembler installs tables, locks, append-only and deferred reverse triggers.
This module checks actual stored inventory/row commitments, not unseen file bytes.
"""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.rdagent_controller_ddl import _schema
from packages.openevolve_structured.upload_contracts import Begin

TABLES = (
    "oe_structured_checkpoint_intents",
    "oe_structured_checkpoint_uploads",
    "oe_structured_checkpoint_chunks",
    "oe_structured_checkpoint_completions",
    "oe_structured_checkpoint_publications",
    "oe_structured_control_states",
    "oe_structured_epochs",
    "oe_structured_specs",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_process_admissions",
    "research_process_states",
    "research_process_receipts",
    "research_objects",
)


def render_upgrade_sql():
    schema = _schema(Begin)
    _require_serialized_keys(schema)
    raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return SQL.replace("@RULE@", raw)


def render_downgrade_sql():
    return "DROP FUNCTION @SCHEMA@.mineagent_oes_checkpoint_check(uuid);"


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_checkpoint_check(intent_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE i @SCHEMA@.oe_structured_checkpoint_intents%ROWTYPE;
 u @SCHEMA@.oe_structured_checkpoint_uploads%ROWTYPE;
 c @SCHEMA@.oe_structured_control_states%ROWTYPE;
 e @SCHEMA@.oe_structured_epochs%ROWTYPE;
 r @SCHEMA@.research_budget_reservations%ROWTYPE;
 paid @SCHEMA@.research_budget_settlements%ROWTYPE;
 done @SCHEMA@.oe_structured_checkpoint_completions%ROWTYPE;
 pub @SCHEMA@.oe_structured_checkpoint_publications%ROWTYPE;
 controller @SCHEMA@.research_process_admissions%ROWTYPE;
 f record; chunk record; inventory jsonb; rule jsonb:='@RULE@'::jsonb;
 expected jsonb; names text[]:=ARRAY[]::text[]; previous text;
 bytes bigint:=0; chunks integer:=0; observed integer:=0; number integer;
 offset_at integer; size_at integer; count_saves integer; max_save integer;
 maximum integer;
BEGIN
 SELECT * INTO STRICT i FROM @SCHEMA@.oe_structured_checkpoint_intents WHERE id=intent_id;
 SELECT * INTO STRICT e FROM @SCHEMA@.oe_structured_epochs WHERE id=i.epoch_id;
 SELECT * INTO STRICT c FROM @SCHEMA@.oe_structured_control_states WHERE id=i.control_state_id;
 SELECT (config#>>'{search,maximum_mutation_slots}')::integer INTO STRICT maximum
 FROM @SCHEMA@.oe_structured_specs WHERE owner_id=i.owner_id AND run_id=i.run_id;
 IF ROW(e.owner_id,e.run_id,e.attempt_id,e.generation) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation)
 OR ROW(c.owner_id,c.run_id,c.attempt_id,c.generation,c.epoch_id) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation,e.id)
 OR ROW(i.frontier,i.state_sha256) IS DISTINCT FROM ROW(c.frontier,c.state_sha256)
 OR c.phase NOT IN ('seed','applied') OR i.save_ordinal NOT BETWEEN 1 AND maximum+2
 THEN RAISE EXCEPTION 'OE checkpoint exact control'; END IF;
 SELECT count(*),max(save_ordinal) INTO count_saves,max_save FROM @SCHEMA@.oe_structured_checkpoint_intents WHERE epoch_id=e.id;
 IF count_saves<>max_save OR max_save>maximum+2
 THEN RAISE EXCEPTION 'OE checkpoint save prefix'; END IF;
 IF EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_intents prior
  WHERE prior.epoch_id=e.id AND prior.save_ordinal<i.save_ordinal AND NOT EXISTS(
   SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_publications pp WHERE pp.intent_id=prior.id
    AND pp.epoch_id=prior.epoch_id AND pp.control_state_id=prior.control_state_id AND pp.state_sha256=prior.state_sha256))
 THEN RAISE EXCEPTION 'OE next save before prior publication'; END IF;
 -- Multiple real saves at the same cursor are legal, including the final save.
 SELECT * INTO u FROM @SCHEMA@.oe_structured_checkpoint_uploads WHERE oe_structured_checkpoint_uploads.intent_id=i.id;
 IF u.id IS NULL THEN
  IF EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_publications WHERE oe_structured_checkpoint_publications.intent_id=i.id)
  THEN RAISE EXCEPTION 'OE publication without upload'; END IF;
  RETURN;
 END IF;
 IF ROW(u.owner_id,u.run_id,u.attempt_id,u.generation,u.epoch_id) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation,e.id)
 THEN RAISE EXCEPTION 'OE upload epoch swap'; END IF;
 inventory:=u.inventory_canonical::jsonb;
 IF @SCHEMA@.mineagent_rd_schema(inventory,rule,coalesce(rule->'$defs','{}'::jsonb),0) IS NOT TRUE
 OR @SCHEMA@.mineagent_structured_raw_commitment(u.inventory_canonical,inventory,u.inventory_sha256,8192) IS NOT TRUE
 OR inventory->>'token' IS DISTINCT FROM i.id::text
 THEN RAISE EXCEPTION 'OE upload inventory'; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(inventory->'files') WITH ORDINALITY item(value,ord) ORDER BY ord LOOP
  IF (previous IS NOT NULL AND (f.value->>'name') COLLATE "C"<=previous COLLATE "C")
  OR NOT ((f.value->>'name') IN ('metadata.json','best_program.json','best_program_info.json')
   OR (f.value->>'name') ~ '^programs/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}[.]json$')
  THEN RAISE EXCEPTION 'OE upload ordered closed names'; END IF;
  previous:=f.value->>'name'; names:=array_append(names,previous);
  bytes:=bytes+(f.value->>'size_bytes')::bigint;
  number:=ceil((f.value->>'size_bytes')::numeric/65536)::integer;
  FOR n IN 0..number-1 LOOP
   offset_at:=n*65536; size_at:=least(65536,(f.value->>'size_bytes')::integer-offset_at);
   SELECT * INTO chunk FROM @SCHEMA@.oe_structured_checkpoint_chunks WHERE upload_id=u.id AND sequence=chunks;
   IF FOUND THEN
    IF ROW(chunk.owner_id,chunk.run_id,chunk.attempt_id,chunk.generation,chunk.epoch_id) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation,e.id)
    OR ROW(chunk.file_name,chunk.offset_bytes,chunk.size_bytes) IS DISTINCT FROM ROW(previous,offset_at,size_at)
    OR chunk.object_kind IS DISTINCT FROM 'oe_structured_checkpoint_chunk'
    OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_objects obj WHERE obj.owner_id=i.owner_id AND obj.id=chunk.object_id
      AND obj.object_kind=chunk.object_kind AND obj.sha256=chunk.object_sha256)
    THEN RAISE EXCEPTION 'OE upload chunk position or object'; END IF;
    observed:=observed+1;
    IF observed<>chunks+1 THEN RAISE EXCEPTION 'OE upload chunk gap'; END IF;
   END IF;
   chunks:=chunks+1;
  END LOOP;
 END LOOP;
 IF NOT ARRAY['metadata.json','best_program.json','best_program_info.json']::text[]<@names
 OR ROW(bytes,cardinality(names),chunks) IS DISTINCT FROM ROW(u.total_bytes::bigint,u.file_count,u.chunk_count)
 OR observed<>(SELECT count(*) FROM @SCHEMA@.oe_structured_checkpoint_chunks WHERE upload_id=u.id)
 THEN RAISE EXCEPTION 'OE upload inventory totals'; END IF;
 SELECT * INTO STRICT r FROM @SCHEMA@.research_budget_reservations WHERE id=u.reservation_id;
 expected:=jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'mutation_slots',0,
 'evaluator_executions',0,'wall_milliseconds',0,'process_slots',0,
 'storage_bytes',2*bytes+2*1048576+2*4096*(u.file_count+u.chunk_count+1));
 IF ROW(r.owner_id,r.run_id,r.attempt_id,r.generation) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation)
 OR r.purpose IS DISTINCT FROM 'object_storage' OR r.logical_key IS DISTINCT FROM 'oe-structured-upload:'||i.id::text
 OR r.amounts IS DISTINCT FROM expected THEN RAISE EXCEPTION 'OE upload exact hold'; END IF;
 SELECT * INTO done FROM @SCHEMA@.oe_structured_checkpoint_completions WHERE upload_id=u.id;
 SELECT * INTO pub FROM @SCHEMA@.oe_structured_checkpoint_publications WHERE oe_structured_checkpoint_publications.intent_id=i.id;
 IF done.id IS NOT NULL THEN
  IF observed<>chunks OR ROW(done.owner_id,done.run_id,done.attempt_id,done.generation,done.epoch_id) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation,e.id)
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_objects obj WHERE obj.owner_id=i.owner_id AND obj.id=done.inventory_object_id
   AND obj.object_kind='oe_structured_checkpoint_inventory' AND obj.sha256=done.inventory_object_sha256)
  THEN RAISE EXCEPTION 'OE completed upload prefix'; END IF;
 END IF;
 IF pub.id IS NOT NULL AND (done.id IS NULL
  OR ROW(pub.owner_id,pub.run_id,pub.attempt_id,pub.generation,pub.epoch_id,pub.completion_id,pub.control_state_id,pub.state_sha256)
   IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation,e.id,done.id,c.id,c.state_sha256))
 THEN RAISE EXCEPTION 'OE checkpoint publication consumer'; END IF;
 SELECT * INTO paid FROM @SCHEMA@.research_budget_settlements WHERE reservation_id=r.id;
 IF r.state='settled' THEN
  SELECT * INTO STRICT controller FROM @SCHEMA@.research_process_admissions WHERE id=e.controller_admission_id;
  IF pub.id IS NULL OR paid.id IS NULL
  OR ROW(controller.owner_id,controller.run_id,controller.attempt_id,controller.generation) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation)
  OR controller.role IS DISTINCT FROM 'openevolve_orchestrator'
  OR controller.profile IS DISTINCT FROM 'mineagent.oe-structured-orchestrator.v1'
  OR count_saves<>maximum+2
  OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_intents tail
   WHERE tail.epoch_id=e.id AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_publications tp WHERE tp.intent_id=tail.id))
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_checkpoint_intents final
   JOIN @SCHEMA@.oe_structured_control_states fc ON fc.id=final.control_state_id
   WHERE final.epoch_id=e.id AND final.save_ordinal=maximum+2 AND final.frontier=maximum
    AND fc.epoch_id=e.id AND fc.phase='applied' AND fc.sequence=2*maximum+1
    AND fc.state_sha256=final.state_sha256)
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts pr WHERE pr.admission_id=controller.id AND pr.kind='controller_completion')
  OR ROW(paid.owner_id,paid.run_id,paid.attempt_id,paid.generation) IS DISTINCT FROM ROW(i.owner_id,i.run_id,i.attempt_id,i.generation)
  OR paid.charge IS DISTINCT FROM expected OR paid.outcome IS DISTINCT FROM 'conservative_unknown'
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_states st WHERE st.admission_id=controller.id AND st.status='stopped')
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts pr WHERE pr.admission_id=controller.id AND pr.kind='stop')
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_reservations cr JOIN @SCHEMA@.research_budget_settlements cs ON cs.reservation_id=cr.id
   WHERE cr.id=controller.reservation_id AND cr.state='settled' AND cs.outcome='conservative_unknown' AND cs.charge=cr.amounts)
  THEN RAISE EXCEPTION 'OE upload premature release'; END IF;
 ELSIF paid.id IS NOT NULL THEN RAISE EXCEPTION 'OE upload uncommitted charge'; END IF;
END $$;
"""
