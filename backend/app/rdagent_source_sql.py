"""Offline RD source semantic SQL fragment; never installed or registered here.

Requires exact model DDL plus the engine-object consumer arbitration DDL and all
unchanged revision-015 guards. The latter deliberately reject RD runtime identity
and request codecs: this fragment is NOT an activation migration or PG acceptance.
Host-read witnesses attest object bytes/parser results; SQL checks their relational
bindings and numeric/hash consistency, not native files or JSONParser execution.

Source object-storage reservations may remain reserved after successful source
publication, until the explicit source-specific settlement. Unlike completed
model transports, this predicate deliberately does not require them settled.

The deferred owner-wide scan is conservative draft coverage, potentially
O(owner history * writes). Activation needs a separately reviewed touched-source
index/locking strategy and real concurrency/performance tests; no scalability or
repeatable-read write-skew acceptance is claimed by rendering these predicates.
"""

import hashlib
import re
from pathlib import Path

# Filled from the reviewed source snapshot, not inferred from caller metadata.
SOURCE_PINS = {
    "backend/app/rdagent_source_witness_services.py": "0f78664b353206246070d22c0332f4529451cd43fabaa727e70198b22c0322d6",
    "backend/app/research_process_models.py": "da0a3e80abaac7d5b4099debeedfabe9bb3ac4cf1817a91909f24d00265fa65d",
    "backend/app/models.py": "80a747e38cd2b126f90572506efd6c3af9ddba28bede697a547a09f01af57615",
    "backend/app/engine_evidence_ddl.py": "401741d6d717dc375c65a7fa9d6c8be035bb2a150e91c5904117fbe1d13c93c4",
    "backend/app/factor_models.py": "a5d3f27f2b8e23048e754b161944bf2f9dde518feff8de716e94748a321dde6b",
    "backend/app/rdagent_models.py": "8c329f76ca273d8f3610ef8a57432a381dfbecdea89d976083f946cd5d15e78e",
    "backend/app/rdagent_source_witness_models.py": "d2396f03edb782e47bfedc1ef0abcdc4ed7fdebc0e2894ccff108d4896518263",
    "backend/app/research_broker_models.py": "89c8bfd83e201eb02909cd5c17af2866d5e2964800fdedbf27757d460fbc1248",
    "backend/app/research_object_models.py": "9eeed4592afe36eecc1c34966814e7109c627633bb4dab505c7ccfe2502ebaf8",
    "packages/rdagent_adapter/response_parser.py": "a6f771c98a7b87be16368a04a5fa957b98b745acb8159f3ec6e0cecb67ae9c5a",
}

# 027 widens only its explicit admission profile gate. The historical018
# renderer remains byte-identical; this exact successor source is reviewed.
SUCCESSOR_SOURCE_PINS = {
    (
        "backend/app/research_process_models.py",
        "da0a3e80abaac7d5b4099debeedfabe9bb3ac4cf1817a91909f24d00265fa65d",
    ): "38c2e17f65672ec49f1d0ac68b922b669e085b16cbad7d56eaa7a1d2c6aca1ca",
}

TRIGGER_TABLES = (
    "factor_generation_sources",
    "factor_generation_starts",
    "factor_upstream_calls",
    "factor_upstream_call_outcomes",
    "factor_generation_model_operations",
    "factor_response_assemblies",
    "factor_response_assembly_parts",
    "factor_source_read_witnesses",
    "factor_source_read_parts",
    "factor_source_publications",
    "factor_source_publication_outcomes",
    "factor_controller_launches",
    "factor_research_specs",
    "factor_research_attempt_bindings",
    "research_process_admissions",
    "factor_versions",
    "factor_objects",
    "research_runs",
    "research_model_operations",
    "research_model_transports",
    "research_model_outcomes",
    "research_objects",
    "research_budget_reservations",
    "research_budget_settlements",
    "engine_object_reservation_consumers",
)


def _scope(alias):
    return f"({alias}.owner_id,{alias}.run_id,{alias}.attempt_id,{alias}.generation)"


def render_sql(schema="public"):
    """Render only after source-pin verification; no connection/execute path."""
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("exact schema identifier required")
    root = Path(__file__).resolve().parents[2]
    for name, expected in SOURCE_PINS.items():
        path = root / name
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError("source-pin symlink")
        if hashlib.sha256(path.read_bytes()).hexdigest() not in (
            expected,
            SUCCESSOR_SOURCE_PINS.get((name, expected)),
        ):
            raise ValueError("source-pin drift: " + name)
    q = '"' + schema + '"'
    sc = _scope("s")
    sql = f"""
-- INCOMPLETE DISABLED REVIEW FRAGMENT. Preserve all existing revision-015 guards.
CREATE FUNCTION {q}.mineagent_rd_ascii_metadata(v jsonb, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text;
BEGIN
 IF depth>8 OR octet_length(v::text)>65536 OR octet_length(v::text)<>char_length(v::text) THEN
  RAISE EXCEPTION 'RD witness metadata grammar bound' USING ERRCODE='23514';
 END IF;
 CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{{'||COALESCE(string_agg(to_jsonb(key)::text||':'||{q}.mineagent_rd_ascii_metadata(value,depth+1),',' ORDER BY key COLLATE "C"),'')||'}}' INTO result FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||COALESCE(string_agg({q}.mineagent_rd_ascii_metadata(value,depth+1),',' ORDER BY ordinal),'')||']' INTO result FROM jsonb_array_elements(v) WITH ORDINALITY AS x(value,ordinal);
 ELSE result:=v::text;
 END CASE;
 RETURN result;
END $$;

CREATE FUNCTION {q}.mineagent_rd_source_violation(source_uuid uuid)
RETURNS text LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE s record; st record; c record; a record; w record; v record; obj record;
 p record; po record; co record; m record; op record; final_outcome record;
 ap record; wp record; r record; count_members integer; count_transports integer;
 selected_first integer; idx integer := 0; previous_id uuid := NULL;
 metadata jsonb; part_documents jsonb := '[]'::jsonb;
 member_ids uuid[] := ARRAY[]::uuid[]; outcome_ids uuid[] := ARRAY[]::uuid[];
BEGIN
 SELECT * INTO s FROM {q}.factor_generation_sources WHERE id=source_uuid;
 IF NOT FOUND THEN RETURN 'source_missing'; END IF;
 IF NOT EXISTS (SELECT 1 FROM {q}.research_runs rr WHERE rr.id=s.run_id AND rr.owner_id=s.owner_id
  AND rr.purpose='factor_research' AND rr.engine='rdagent') THEN RETURN 'run_identity'; END IF;
 SELECT * INTO st FROM {q}.factor_generation_starts WHERE id=s.start_id;
 IF NOT FOUND OR {_scope("st")} IS DISTINCT FROM {sc} OR st.context_id<>s.context_id THEN RETURN 'start_scope'; END IF;
 SELECT * INTO c FROM {q}.factor_upstream_calls WHERE id=s.call_id;
 IF NOT FOUND OR {_scope("c")} IS DISTINCT FROM {sc} OR (c.start_id,c.context_id,c.phase,c.kind)
  IS DISTINCT FROM (s.start_id,s.context_id,'generation'::varchar,'chat'::varchar) THEN RETURN 'generation_call'; END IF;
 SELECT * INTO a FROM {q}.factor_response_assemblies WHERE id=s.assembly_id;
 IF NOT FOUND OR {_scope("a")} IS DISTINCT FROM {sc} OR (a.start_id,a.context_id,a.call_id)
  IS DISTINCT FROM (s.start_id,s.context_id,s.call_id) THEN RETURN 'assembly_scope'; END IF;
 SELECT * INTO w FROM {q}.factor_source_read_witnesses WHERE source_id=s.id;
 IF NOT FOUND OR {_scope("w")} IS DISTINCT FROM {sc} OR (w.assembly_id,w.call_id,w.source_sha256,w.part_count)
  IS DISTINCT FROM (a.id,c.id,s.source_sha256,a.part_count) THEN RETURN 'source_read_witness'; END IF;
 IF w.parser_profile<>'mineagent.rdagent-source-parser.v1' OR w.parser_source_sha256<>
  'a5a6a4a397f86b71caac2b1e056d200411c9d6254dff5e81d84aad8c19d1a583' THEN RETURN 'parser_identity'; END IF;
 SELECT * INTO co FROM {q}.factor_upstream_call_outcomes WHERE call_id=c.id;
 IF NOT FOUND OR {_scope("co")} IS DISTINCT FROM {sc} OR co.status<>'completed'
  OR co.controller_launch_id<>w.controller_launch_id THEN RETURN 'call_completion'; END IF;
 IF NOT EXISTS (SELECT 1 FROM {q}.factor_controller_launches cl WHERE cl.id=w.controller_launch_id
  AND {_scope("cl")}={sc}) THEN RETURN 'controller_scope'; END IF;
 IF NOT EXISTS (SELECT 1 FROM {q}.factor_controller_launches cl
  JOIN {q}.research_process_admissions pa ON pa.id=cl.admission_id
  JOIN {q}.factor_research_attempt_bindings ab ON ab.id=cl.attempt_binding_id
  JOIN {q}.factor_research_specs sp ON sp.id=ab.spec_id
  JOIN {q}.research_runs rr ON rr.id=s.run_id
  WHERE cl.id=w.controller_launch_id AND {_scope("pa")}={sc} AND {_scope("ab")}={sc}
  AND sp.owner_id=s.owner_id AND sp.run_id=s.run_id AND sp.id=st.spec_id AND rr.config=sp.config
  AND pa.role='rd_orchestrator' AND pa.profile='mineagent.rdagent-orchestrator.v1'
  AND pa.input_sha256=cl.bootstrap_sha256 AND pa.logical_operation_id=ab.id
  AND pa.image_id=sp.config->'runtime'->>'orchestrator_image') THEN RETURN 'controller_admission'; END IF;
 SELECT * INTO v FROM {q}.factor_versions WHERE id=s.factor_version_id;
 IF NOT FOUND OR v.owner_id<>s.owner_id OR v.code_sha256<>s.source_sha256 OR v.source_kind<>'rdagent'
  OR v.parent_version_id IS DISTINCT FROM st.parent_factor_version_id
  OR v.source_event_key IS DISTINCT FROM 'rdagent:'||s.start_id::text
  OR v.source_provenance->>'run_id' IS DISTINCT FROM s.run_id::text
  OR v.source_provenance->>'start_id' IS DISTINCT FROM s.start_id::text
  OR v.source_provenance->>'call_id' IS DISTINCT FROM s.call_id::text
  OR v.source_provenance->>'assembly_sha256' IS DISTINCT FROM a.object_sha256
  OR encode(sha256(convert_to(v.code,'UTF8')),'hex')<>s.source_sha256
  OR octet_length(v.code)<>w.source_size_bytes THEN RETURN 'factor_version'; END IF;
 SELECT * INTO obj FROM {q}.factor_objects WHERE id=v.code_object_id;
 IF NOT FOUND OR (obj.owner_id,obj.object_kind,obj.media_type,obj.sha256,obj.size_bytes,obj.id)
  IS DISTINCT FROM (s.owner_id,'code'::varchar,'text/plain'::varchar,s.source_sha256,w.source_size_bytes::bigint,w.source_object_id)
  OR w.source_object_kind<>'code' THEN RETURN 'source_code_object'; END IF;
 SELECT * INTO p FROM {q}.factor_source_publications WHERE start_id=s.start_id;
 IF NOT FOUND OR {_scope("p")} IS DISTINCT FROM {sc} OR
  (p.call_id,p.assembly_object_id,p.assembly_object_kind,p.assembly_sha256,p.source_sha256,p.size_bytes,p.factor_id,p.factor_version_id)
  IS DISTINCT FROM (c.id,a.object_id,a.object_kind,a.object_sha256,s.source_sha256,w.source_size_bytes,v.factor_id,v.id)
  THEN RETURN 'source_publication'; END IF;
 SELECT * INTO po FROM {q}.factor_source_publication_outcomes WHERE source_id=s.id;
 IF NOT FOUND OR {_scope("po")} IS DISTINCT FROM {sc} OR po.publication_id<>p.id THEN RETURN 'source_publication_outcome'; END IF;
 SELECT * INTO r FROM {q}.research_budget_reservations WHERE id=p.budget_reservation_id;
 IF NOT FOUND OR {_scope("r")} IS DISTINCT FROM {sc} OR r.purpose<>'object_storage'
  OR r.logical_key<>'rdsource:'||s.start_id::text OR (r.amounts->>'storage_bytes')::numeric IS DISTINCT FROM p.peak_bytes::numeric
  OR EXISTS (SELECT 1 FROM jsonb_each(r.amounts) x WHERE x.key<>'storage_bytes' AND x.value<>'0'::jsonb)
  THEN RETURN 'source_reservation'; END IF;
 IF NOT EXISTS (SELECT 1 FROM {q}.engine_object_reservation_consumers ec WHERE ec.reservation_id=r.id
  AND {_scope("ec")}={sc} AND ec.consumer_kind='factor_source' AND ec.consumer_id=p.id)
  THEN RETURN 'source_consumer_claim'; END IF;
 SELECT count(*),max(request_ordinal) FILTER (WHERE relation<>'continuation') INTO count_members,selected_first
  FROM {q}.factor_generation_model_operations WHERE call_id=c.id;
 IF count_members NOT BETWEEN 1 AND 60 OR selected_first IS NULL OR
  count_members-selected_first NOT BETWEEN 1 AND 6 OR co.member_count<>count_members
  OR w.selected_first_ordinal<>selected_first OR w.part_count<>count_members-selected_first THEN RETURN 'member_cardinality'; END IF;
 FOR m IN SELECT * FROM {q}.factor_generation_model_operations WHERE call_id=c.id ORDER BY request_ordinal LOOP
  IF {_scope("m")} IS DISTINCT FROM {sc} OR (m.start_id,m.context_id,m.protocol)
   IS DISTINCT FROM (s.start_id,s.context_id,'mineagent.rdagent-chat.v1'::varchar)
   OR m.request_ordinal<>idx OR m.predecessor_id IS DISTINCT FROM previous_id
   OR (idx=0 AND m.relation<>'initial') OR (idx>0 AND m.relation NOT IN ('continuation','semantic_repair'))
   THEN RETURN 'member_order_scope'; END IF;
  SELECT * INTO op FROM {q}.research_model_operations WHERE id=m.operation_id;
  IF NOT FOUND OR {_scope("op")} IS DISTINCT FROM {sc} OR op.context_id<>s.context_id OR op.state<>'completed'
   OR op.protocol<>m.protocol
   OR op.purpose<>(CASE WHEN m.relation='continuation' THEN 'continuation' ELSE 'chat' END)
   OR op.request->>'kind' IS DISTINCT FROM 'chat' OR op.request->>'model' IS DISTINCT FROM 'fixture.rdagent.v1'
   OR op.request->'response_format' IS DISTINCT FROM '{{"type":"json_object"}}'::jsonb THEN RETURN 'operation_scope'; END IF;
  SELECT count(*) INTO count_transports FROM {q}.research_model_transports WHERE operation_id=op.id;
  IF count_transports NOT BETWEEN 1 AND 256 OR EXISTS (
   SELECT 1 FROM {q}.research_model_transports t LEFT JOIN {q}.research_model_outcomes o ON o.transport_id=t.id
    LEFT JOIN {q}.research_budget_reservations br ON br.id=t.reservation_id
    LEFT JOIN {q}.research_budget_settlements bs ON bs.reservation_id=br.id
   WHERE t.operation_id=op.id AND ({_scope("t")} IS DISTINCT FROM {sc} OR {_scope("o")} IS DISTINCT FROM {sc}
    OR {_scope("br")} IS DISTINCT FROM {sc} OR {_scope("bs")} IS DISTINCT FROM {sc}
    OR t.ordinal NOT BETWEEN 1 AND count_transports OR br.purpose<>'model_transport' OR br.state<>'settled'
    OR t.state<>(CASE WHEN t.ordinal=count_transports THEN 'completed' ELSE 'failed' END)
    OR o.outcome<>t.state OR o.usage IS DISTINCT FROM bs.charge OR bs.outcome<>'measured'
    OR EXISTS (SELECT 1 FROM jsonb_each(bs.charge) amount WHERE amount.value::text::numeric>(br.amounts->>amount.key)::numeric)
    OR (o.usage->>'calls')::numeric<>1 OR (br.amounts->>'calls')::numeric<>1)) THEN RETURN 'physical_transport_closure'; END IF;
  SELECT o.* INTO final_outcome FROM {q}.research_model_transports t
   JOIN {q}.research_model_outcomes o ON o.transport_id=t.id
   WHERE t.operation_id=op.id AND t.ordinal=count_transports AND o.outcome='completed';
  IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM {q}.research_objects ro
    WHERE ro.id=final_outcome.response_object_id AND (ro.owner_id,ro.object_kind,ro.sha256)
    =(s.owner_id,'model_response'::varchar,final_outcome.response_sha256)) THEN RETURN 'completed_response'; END IF;
  member_ids:=array_append(member_ids,m.id); outcome_ids:=array_append(outcome_ids,final_outcome.id);
  IF idx>=selected_first THEN
   SELECT * INTO ap FROM {q}.factor_response_assembly_parts WHERE assembly_id=a.id AND ordinal=idx-selected_first;
   IF NOT FOUND OR {_scope("ap")} IS DISTINCT FROM {sc} OR
    (ap.call_id,ap.member_id,ap.outcome_id,ap.response_object_id,ap.response_object_kind,ap.response_sha256)
    IS DISTINCT FROM (c.id,m.id,final_outcome.id,final_outcome.response_object_id,'model_response'::varchar,final_outcome.response_sha256)
    THEN RETURN 'selected_assembly_part'; END IF;
   SELECT * INTO wp FROM {q}.factor_source_read_parts WHERE witness_id=w.id AND ordinal=idx-selected_first;
   IF NOT FOUND OR {_scope("wp")} IS DISTINCT FROM {sc} OR
    (wp.assembly_part_id,wp.member_id,wp.outcome_id,wp.response_object_id,wp.response_object_kind,wp.response_sha256)
    IS DISTINCT FROM (ap.id,m.id,final_outcome.id,ap.response_object_id,ap.response_object_kind,ap.response_sha256)
    OR wp.finish_reason<>(CASE WHEN idx=count_members-1 THEN 'stop' ELSE 'length' END)
    THEN RETURN 'selected_read_part'; END IF;
   part_documents:=part_documents||jsonb_build_array(to_jsonb(wp)-ARRAY['id','created_at','owner_id','run_id','attempt_id','generation','witness_id']);
  END IF;
  previous_id:=m.id; idx:=idx+1;
 END LOOP;
 IF co.member_ids IS DISTINCT FROM to_jsonb(member_ids) OR co.response_outcome_ids IS DISTINCT FROM to_jsonb(outcome_ids)
  THEN RETURN 'completed_call_inventory'; END IF;
 IF (SELECT count(*) FROM {q}.factor_response_assembly_parts WHERE assembly_id=a.id)<>w.part_count OR
  (SELECT count(*) FROM {q}.factor_source_read_parts WHERE witness_id=w.id)<>w.part_count OR
  (SELECT sum(content_size_bytes) FROM {q}.factor_source_read_parts WHERE witness_id=w.id) IS DISTINCT FROM w.raw_text_bytes::bigint
  THEN RETURN 'selected_inventory_size'; END IF;
 metadata:=jsonb_build_object('schema_version','mineagent.source-read-witness.v1',
  'scope',jsonb_build_object('owner_id',s.owner_id,'run_id',s.run_id,'attempt_id',s.attempt_id,'generation',s.generation),
  'header',to_jsonb(w)-ARRAY['id','created_at','owner_id','run_id','attempt_id','generation','witness_sha256'],
  'parts',part_documents);
 IF encode(sha256(convert_to({q}.mineagent_rd_ascii_metadata(metadata),'UTF8')),'hex')<>w.witness_sha256
  THEN RETURN 'witness_metadata_digest'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION {q}.mineagent_rd_source_deferred() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE sid uuid; violation text; owner_uuid uuid;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 FOR sid IN SELECT id FROM {q}.factor_generation_sources WHERE owner_id=owner_uuid LOOP
  violation:={q}.mineagent_rd_source_violation(sid);
  IF violation IS NOT NULL THEN RAISE EXCEPTION 'RD source closure: %',violation USING ERRCODE='23514'; END IF;
 END LOOP;
 IF TG_OP='UPDATE' AND OLD.owner_id IS DISTINCT FROM NEW.owner_id THEN
  FOR sid IN SELECT id FROM {q}.factor_generation_sources WHERE owner_id=OLD.owner_id LOOP
   violation:={q}.mineagent_rd_source_violation(sid);
   IF violation IS NOT NULL THEN RAISE EXCEPTION 'RD source closure: %',violation USING ERRCODE='23514'; END IF;
  END LOOP;
 END IF;
 RETURN NULL;
END $$;
"""
    for table in TRIGGER_TABLES:
        sql += f'\nCREATE CONSTRAINT TRIGGER rd_source_semantics AFTER INSERT OR UPDATE OR DELETE ON {q}."{table}" DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {q}.mineagent_rd_source_deferred();\n'
    return sql
