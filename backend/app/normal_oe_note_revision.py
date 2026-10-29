"""032 additive normal-result note pins; legacy note validation stays installed."""

import hashlib
import importlib

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app.normal_oe_note_identity_sql import render_identity_sql
from backend.app.normal_oe_note_models import NormalOENotePin

KINDS = "'artifact','dataset','universe','experiment','run','evaluation','comparison','strategy_version','candidate','spec_revision','controlled_experiment'"


def kind_sql(upgrade):
    values = KINDS + (",'normal_oe_execution'" if upgrade else "")
    return f"""ALTER TABLE @SCHEMA@.research_note_evidence_pins DROP CONSTRAINT research_note_evidence_pins_resource_kind_check;
ALTER TABLE @SCHEMA@.research_note_evidence_pins ADD CONSTRAINT research_note_evidence_pins_resource_kind_check CHECK (resource_kind IN ({values}));
"""


def render_table_sql():
    for name in ("oe_structured_models", "execution_snapshot_models", "factor_models"):
        importlib.import_module("backend.app." + name)
    table = NormalOENotePin.__table__
    sql = str(
        CreateTable(table, include_foreign_key_constraints=[]).compile(
            dialect=dialect()
        )
    ).strip()
    sql = (
        sql.replace(
            "CREATE TABLE research_note_normal_oe_pins (",
            "CREATE TABLE @SCHEMA@.research_note_normal_oe_pins (",
            1,
        )
        + ";\n"
    )
    for fk in sorted(
        table.foreign_key_constraints,
        key=lambda k: tuple(e.parent.name for e in k.elements),
    ):
        local = ",".join(e.parent.name for e in fk.elements)
        target = fk.referred_table.name
        remote = ",".join(e.column.name for e in fk.elements)
        name = (
            "note032_fk_"
            + hashlib.sha256(f"{local}:{target}:{remote}".encode()).hexdigest()[:20]
        )
        sql += f"ALTER TABLE @SCHEMA@.research_note_normal_oe_pins ADD CONSTRAINT {name} FOREIGN KEY ({local}) REFERENCES @SCHEMA@.{target} ({remote}) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;\n"
    return sql


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_normal_note_pin(pid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE p research_note_evidence_pins%ROWTYPE; a research_note_normal_oe_pins%ROWTYPE;
 e oe_structured_executions%ROWTYPE; o oe_structured_engine_outcomes%ROWTYPE;
 c oe_structured_candidates%ROWTYPE; s oe_structured_specs%ROWTYPE;
 r research_runs%ROWTYPE; v factor_versions%ROWTYPE;
 sp factor_execution_snapshot_publications%ROWTYPE; so factor_execution_snapshot_outcomes%ROWTYPE;
 sr research_runs%ROWTYPE; d dataset_versions%ROWTYPE; x data_usage_exposures%ROWTYPE;
 details jsonb; evidence jsonb; source_evidence jsonb; role text; xid uuid; did uuid;
BEGIN
 SELECT * INTO STRICT p FROM research_note_evidence_pins WHERE id=pid;
 SELECT * INTO a FROM research_note_normal_oe_pins WHERE pin_id=pid;
 IF p.resource_kind<>'normal_oe_execution' THEN
  IF FOUND THEN RAISE EXCEPTION 'Legacy note pin cannot have normal attachment'; END IF;
  RETURN;
 END IF;
 IF NOT FOUND OR (a.owner_id,a.execution_id) IS DISTINCT FROM (p.owner_id,p.resource_id)
 THEN RAISE EXCEPTION 'Normal note requires exact attachment'; END IF;
 SELECT * INTO STRICT e FROM oe_structured_executions WHERE id=a.execution_id;
 SELECT * INTO STRICT o FROM oe_structured_engine_outcomes WHERE id=a.engine_outcome_id;
 SELECT * INTO STRICT c FROM oe_structured_candidates WHERE id=e.candidate_id;
 SELECT * INTO STRICT s FROM oe_structured_specs WHERE run_id=e.run_id;
 SELECT * INTO STRICT r FROM research_runs WHERE id=e.run_id;
 SELECT * INTO STRICT sp FROM factor_execution_snapshot_publications WHERE id=e.snapshot_id;
 SELECT * INTO STRICT so FROM factor_execution_snapshot_outcomes WHERE publication_id=sp.id;
 SELECT * INTO STRICT v FROM factor_versions WHERE id=sp.factor_version_id;
 SELECT * INTO STRICT sr FROM research_runs WHERE id=sp.run_id;
 IF (e.owner_id,e.run_id,e.attempt_id,e.generation) IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR (o.owner_id,o.run_id,o.attempt_id,o.generation,o.execution_id,o.candidate_id)
 IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation,e.id,c.id)
 OR (c.owner_id,c.run_id,c.attempt_id,c.generation,c.program_version_id)
 IS DISTINCT FROM (a.owner_id,a.run_id,a.attempt_id,a.generation,e.program_version_id)
 OR s.owner_id IS DISTINCT FROM a.owner_id OR r.owner_id IS DISTINCT FROM a.owner_id
 OR sp.owner_id IS DISTINCT FROM a.owner_id OR v.owner_id IS DISTINCT FROM a.owner_id
 OR sr.owner_id IS DISTINCT FROM a.owner_id
 OR r.status NOT IN ('succeeded','failed','cancelled','interrupted')
 THEN RAISE EXCEPTION 'Normal note owned completed graph differs'; END IF;
 PERFORM mineagent_oes_graph_check(e.run_id);
 PERFORM mineagent_execution_snapshot_check(sp.id);
 details:=jsonb_build_object('schema_version','mineagent.note-normal-oe-pin.v1',
 'run_id',a.run_id::text,'attempt_id',a.attempt_id::text,'generation',a.generation,
 'candidate_id',c.id::text,'program_version_id',e.program_version_id::text,'program_sha256',c.program_sha256,
 'factor_id',v.factor_id::text,'factor_version_id',v.id::text,'snapshot_id',sp.id::text,
 'snapshot_input_sha256',so.input_sha256,'market_sha256',e.market_sha256,
 'config_sha256',s.config_sha256,'measurement_sha256',o.measurement_sha256);
 IF p.details IS DISTINCT FROM details OR p.details->>'generation' IS DISTINCT FROM '1'
 OR p.sha256 IS DISTINCT FROM o.result_sha256
 THEN RAISE EXCEPTION 'Normal note exact retained identity differs'; END IF;
 source_evidence:=jsonb_build_object('run_id',sp.run_id::text,'execution_snapshot_id',sp.id::text,
 'manifest_sha256',sp.manifest_sha256,'input_sha256',so.input_sha256,'output_sha256',so.output_sha256,
 'extent','conservative_full_retained_dataset','qualification','historical_disclosure_not_live_execution_authority');
 evidence:=jsonb_build_object('schema_version','mineagent.oe-structured-result-release.v1',
 'run_id',r.id::text,'execution_id',e.id::text,'candidate_id',c.id::text,
 'structured_strategy_version_id',e.program_version_id::text,'factor_version_id',v.id::text,
 'factor_id',v.factor_id::text,'execution_snapshot_id',sp.id::text,'snapshot_source',source_evidence,
 'config_sha256',s.config_sha256,'program_sha256',c.program_sha256,'strategy_input_sha256',e.strategy_input_sha256,
 'market_sha256',e.market_sha256,'result_sha256',o.result_sha256,'measurement_sha256',o.measurement_sha256,
 'objective_sha256',o.objective_sha256,'extent','conservative_full_retained_dataset_including_history_and_forward_labels',
 'qualification','fixed_snapshot_comparison_not_causal_gain');
 FOREACH role IN ARRAY ARRAY['factor','market'] LOOP
  xid:=CASE role WHEN 'factor' THEN a.factor_exposure_id ELSE a.market_exposure_id END;
  did:=CASE role WHEN 'factor' THEN sr.dataset_version_id ELSE s.dataset_id END;
  SELECT * INTO STRICT d FROM dataset_versions WHERE id=did;
  SELECT * INTO STRICT x FROM data_usage_exposures WHERE id=xid;
  IF d.owner_id IS DISTINCT FROM a.owner_id
  OR (x.owner_id,x.run_id,x.experiment_id,x.split_version_id,x.dataset_version_id,x.start_date,x.end_date,x.purpose,x.source_key)
  IS DISTINCT FROM (a.owner_id,r.id,r.experiment_id,r.split_version_id,d.id,d.start_date,d.end_date,
    'engine_structured_execution_release'::varchar,
    'engine-result:oe_structured_execution:'||e.id::text||':'||role||':v1')
  OR x.identities IS DISTINCT FROM mineagent_normal_note_dataset_identities(d.id)
  OR x.provenance IS DISTINCT FROM evidence||jsonb_build_object('role',role,'dataset_version_id',d.id::text,'dataset_content_sha256',d.content_sha256)
  THEN RAISE EXCEPTION 'Normal note requires exact prior role release'; END IF;
 END LOOP;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_normal_note_revision(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_note_revisions%ROWTYPE; ref jsonb; p research_note_evidence_pins%ROWTYPE;
 expected jsonb; actual jsonb; selected uuid[]:='{}';
BEGIN
 SELECT * INTO STRICT r FROM research_note_revisions WHERE id=rid;
 IF jsonb_typeof(r.content->'evidence') IS DISTINCT FROM 'array' THEN
  IF EXISTS(SELECT 1 FROM research_note_evidence_pins WHERE revision_id=rid AND resource_kind='normal_oe_execution')
  OR r.content#>>'{restore,schema_version}'='mineagent.note-normal-oe-restore.v1'
  THEN RAISE EXCEPTION 'Normal note requires typed evidence array'; END IF;
  RETURN;
 END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(r.content->'evidence') LOOP
  IF ref->>'kind' IS NULL OR ref->>'kind' NOT IN ('normal_oe_execution','normal_oe_decision','normal_oe_fill') THEN CONTINUE; END IF;
  SELECT * INTO STRICT p FROM research_note_evidence_pins WHERE revision_id=r.id
    AND resource_kind='normal_oe_execution' AND resource_id=(ref->>'execution_id')::uuid;
  IF p.owner_id IS DISTINCT FROM r.owner_id THEN RAISE EXCEPTION 'Normal note revision owner differs'; END IF;
  expected:=(p.details-'schema_version'-'attempt_id')||jsonb_build_object('owner_id',p.owner_id::text,
    'execution_id',p.resource_id::text,'result_sha256',p.sha256,'execution_attempt_id',p.details->>'attempt_id');
  actual:=ref-ARRAY['kind','label'];
  IF jsonb_typeof(ref->'label') IS DISTINCT FROM 'string' OR length(ref->>'label')>200
  THEN RAISE EXCEPTION 'Normal note label differs'; END IF;
  IF ref->>'kind'='normal_oe_decision' THEN
   IF jsonb_typeof(ref->'decision_sequence') IS DISTINCT FROM 'number'
   OR (ref->>'decision_sequence') !~ '^[0-7]$'
   OR jsonb_typeof(ref->'row_sha256') IS DISTINCT FROM 'string' OR (ref->>'row_sha256') !~ '^[a-f0-9]{64}$'
   THEN RAISE EXCEPTION 'Normal note decision selector differs'; END IF;
   actual:=actual-ARRAY['decision_sequence','row_sha256'];
  ELSIF ref->>'kind'='normal_oe_fill' THEN
   IF jsonb_typeof(ref->'native_fill_id') IS DISTINCT FROM 'string' OR length(ref->>'native_fill_id') NOT BETWEEN 1 AND 200
   OR (ref->>'native_fill_id') ~ '[[:cntrl:]]'
   OR jsonb_typeof(ref->'listing_id') IS DISTINCT FROM 'string' OR (ref->>'listing_id')::uuid::text IS DISTINCT FROM ref->>'listing_id'
   OR jsonb_typeof(ref->'session_date') IS DISTINCT FROM 'string' OR (ref->>'session_date') !~ '^\d{4}-\d{2}-\d{2}$'
   OR (ref->>'session_date')::date::text IS DISTINCT FROM ref->>'session_date'
   OR jsonb_typeof(ref->'row_sha256') IS DISTINCT FROM 'string' OR (ref->>'row_sha256') !~ '^[a-f0-9]{64}$'
   THEN RAISE EXCEPTION 'Normal note fill selector differs'; END IF;
   actual:=actual-ARRAY['native_fill_id','listing_id','session_date','row_sha256'];
  END IF;
  IF actual IS DISTINCT FROM expected OR ref->>'generation' IS DISTINCT FROM '1'
  THEN RAISE EXCEPTION 'Normal note typed reference differs from pin'; END IF;
  selected:=array_append(selected,p.id);
  PERFORM mineagent_normal_note_pin(p.id);
 END LOOP;
 IF r.content#>>'{restore,schema_version}'='mineagent.note-normal-oe-restore.v1' THEN
  IF jsonb_typeof(r.content#>'{restore,evidence_index}') IS DISTINCT FROM 'number'
  OR (r.content#>>'{restore,evidence_index}') !~ '^(0|[1-9][0-9]*)$'
  OR (r.content#>>'{restore,evidence_index}')::numeric NOT BETWEEN 0 AND 31
  OR r.content->'restore' IS DISTINCT FROM jsonb_build_object('schema_version','mineagent.note-normal-oe-restore.v1',
    'evidence_index',r.content#>'{restore,evidence_index}')
  THEN RAISE EXCEPTION 'Normal note restore selector differs'; END IF;
  IF coalesce((r.content->'evidence'->((r.content#>>'{restore,evidence_index}')::integer)->>'kind')
   IN ('normal_oe_execution','normal_oe_decision','normal_oe_fill'),false) IS NOT TRUE
  THEN RAISE EXCEPTION 'Normal note restore needs exact normal evidence'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM research_note_evidence_pins WHERE revision_id=rid
  AND resource_kind='normal_oe_execution' AND NOT(id=ANY(selected)))
 THEN RAISE EXCEPTION 'Normal note pin lacks typed source reference'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_normal_note_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rid uuid; pid uuid;
BEGIN
 IF TG_TABLE_NAME='research_note_revisions' THEN rid:=NEW.id;
 ELSIF TG_TABLE_NAME='research_note_evidence_pins' THEN
  rid:=NEW.revision_id; PERFORM mineagent_normal_note_pin(NEW.id);
 ELSE
  SELECT revision_id INTO STRICT rid FROM research_note_evidence_pins WHERE id=NEW.pin_id;
  PERFORM mineagent_normal_note_pin(NEW.pin_id);
 END IF;
 PERFORM mineagent_normal_note_revision(rid);
 RETURN NULL;
END $$;
CREATE TRIGGER immutable_normal_note_pin BEFORE UPDATE OR DELETE ON @SCHEMA@.research_note_normal_oe_pins
FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.reject_research_note_change();
"""


def render_upgrade_sql():
    out = kind_sql(True) + render_identity_sql() + SQL
    for table in (
        "research_note_revisions",
        "research_note_evidence_pins",
        "research_note_normal_oe_pins",
    ):
        out += f"CREATE CONSTRAINT TRIGGER normal_note_consistent AFTER INSERT ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_normal_note_consistent();\n"
    return out


def render_downgrade_sql():
    out = """DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_note_normal_oe_pins)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_note_evidence_pins WHERE resource_kind='normal_oe_execution')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_note_revisions WHERE content#>>'{restore,schema_version}'='mineagent.note-normal-oe-restore.v1')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_note_revisions r, LATERAL jsonb_array_elements(r.content->'evidence') e WHERE e->>'kind' IN ('normal_oe_execution','normal_oe_decision','normal_oe_fill'))
 THEN RAISE EXCEPTION 'Normal note history prevents downgrade'; END IF;
END $$;
"""
    for table in (
        "research_note_revisions",
        "research_note_evidence_pins",
        "research_note_normal_oe_pins",
    ):
        out += f"DROP TRIGGER normal_note_consistent ON @SCHEMA@.{table};\n"
    return (
        out
        + """DROP TABLE @SCHEMA@.research_note_normal_oe_pins;
DROP FUNCTION @SCHEMA@.mineagent_normal_note_consistent();
DROP FUNCTION @SCHEMA@.mineagent_normal_note_revision(uuid);
DROP FUNCTION @SCHEMA@.mineagent_normal_note_pin(uuid);
DROP FUNCTION @SCHEMA@.mineagent_normal_note_dataset_identities(uuid);
DROP FUNCTION @SCHEMA@.mineagent_normal_note_upper(text,boolean);
"""
        + kind_sql(False)
    )
