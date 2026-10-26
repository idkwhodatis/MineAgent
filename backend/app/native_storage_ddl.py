"""Frozen native table DDL draft; migration copies reviewed literals, never imports this."""

TABLE_SQL = "\nCREATE TABLE factor_native_publications (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tcutoff_id UUID NOT NULL, \n\tadmission_id UUID NOT NULL, \n\treservation_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\tsource_object_id UUID NOT NULL, \n\tsource_sha256 VARCHAR(64) NOT NULL, \n\tstop_receipt_id UUID NOT NULL, \n\textraction_receipt_id UUID NOT NULL, \n\tstop_kind VARCHAR(30) NOT NULL, \n\textraction_kind VARCHAR(30) NOT NULL, \n\tintent JSONB NOT NULL, \n\tintent_sha256 VARCHAR(64) NOT NULL, \n\tobject_id UUID NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tjournal_key VARCHAR(300) NOT NULL, \n\tpeak_bytes BIGINT NOT NULL, \n\tpeak_entries INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tUNIQUE (owner_id, id, object_id, sha256, size_bytes, storage_key), \n\tUNIQUE (reservation_id), \n\tUNIQUE (admission_id), \n\tUNIQUE (object_id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id) REFERENCES research_process_admissions (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, source_object_id, source_sha256) REFERENCES factor_objects (owner_id, id, sha256), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, source_id) REFERENCES factor_generation_sources (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id, stop_receipt_id, stop_kind) REFERENCES research_process_receipts (owner_id, run_id, attempt_id, generation, admission_id, id, kind), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id, extraction_receipt_id, extraction_kind) REFERENCES research_process_receipts (owner_id, run_id, attempt_id, generation, admission_id, id, kind), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, cutoff_id, admission_id, source_id) REFERENCES factor_cutoff_executions (owner_id, run_id, attempt_id, generation, id, candidate_admission_id, evaluated_source_id), \n\tCHECK (stop_kind = 'stop' AND extraction_kind = 'extraction'), \n\tCHECK (generation > 0 AND size_bytes BETWEEN 1 AND 16777216), \n\tCHECK (sha256 ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$'), \n\tCHECK (jsonb_typeof(intent) = 'object' AND octet_length(intent::text) <= 131072), \n\tCHECK (peak_bytes = 2 * size_bytes + 131072 AND peak_entries = 8), \n\tCHECK (storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || '.h5'), \n\tCHECK (journal_key = 'factors/' || owner_id::text || '/native-journals/' || id::text || '.json'), \n\tCHECK (stop_receipt_id <> extraction_receipt_id)\n)\n\n;\n\nCREATE TABLE factor_native_objects (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tobject_kind VARCHAR(40) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tmedia_type VARCHAR(100) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, id, sha256, size_bytes, storage_key), \n\tCHECK (object_kind = 'native_hdf' AND media_type = 'application/x-hdf5'), \n\tCHECK (size_bytes BETWEEN 1 AND 16777216 AND sha256 ~ '^[a-f0-9]{64}$'), \n\tCHECK (storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || '.h5')\n)\n\n;\n\nCREATE TABLE factor_native_outcomes (\n\tpublication_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tobject_id UUID NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tjournal_sha256 VARCHAR(64) NOT NULL, \n\tphysical_receipt_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (publication_id), \n\tFOREIGN KEY(owner_id, publication_id, object_id, sha256, size_bytes, storage_key) REFERENCES factor_native_publications (owner_id, id, object_id, sha256, size_bytes, storage_key), \n\tFOREIGN KEY(owner_id, object_id, sha256, size_bytes, storage_key) REFERENCES factor_native_objects (owner_id, id, sha256, size_bytes, storage_key), \n\tCHECK (journal_sha256 ~ '^[a-f0-9]{64}$' AND physical_receipt_sha256 ~ '^[a-f0-9]{64}$')\n)\n\n;\n\nCREATE TABLE factor_native_normalizer_inputs (\n\tadmission_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tpublication_id UUID NOT NULL, \n\treservation_id UUID NOT NULL, \n\tobject_id UUID NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tinput_sha256 VARCHAR(64) NOT NULL, \n\tintent_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (admission_id), \n\tUNIQUE (publication_id), \n\tUNIQUE (reservation_id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id) REFERENCES research_process_admissions (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, publication_id) REFERENCES factor_native_publications (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, publication_id, object_id, sha256, size_bytes, storage_key) REFERENCES factor_native_publications (owner_id, id, object_id, sha256, size_bytes, storage_key), \n\tCHECK (generation > 0 AND input_sha256 ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$')\n)\n\n;"

SEMANTIC_SQL = r"""
CREATE FUNCTION mineagent_native_check(pid uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE p factor_native_publications%ROWTYPE;
 a research_process_admissions%ROWTYPE; r research_budget_reservations%ROWTYPE;
 st research_process_receipts%ROWTYPE; ex research_process_receipts%ROWTYPE;
 li research_process_receipts%ROWTYPE; ident research_process_receipts%ROWTYPE;
 c factor_cutoff_executions%ROWTYPE;
 ev factor_evaluation_starts%ROWTYPE; o factor_native_outcomes%ROWTYPE;
 obj factor_native_objects%ROWTYPE; j jsonb; output jsonb;
BEGIN
 SELECT * INTO STRICT p FROM factor_native_publications WHERE id=pid;
 SELECT * INTO STRICT a FROM research_process_admissions WHERE id=p.admission_id;
 SELECT * INTO STRICT r FROM research_budget_reservations WHERE id=p.reservation_id;
 SELECT * INTO STRICT st FROM research_process_receipts WHERE id=p.stop_receipt_id;
 SELECT * INTO STRICT ex FROM research_process_receipts WHERE id=p.extraction_receipt_id;
 SELECT * INTO STRICT li FROM research_process_receipts WHERE admission_id=p.admission_id AND kind='launch_intent';
 SELECT * INTO STRICT ident FROM research_process_receipts WHERE admission_id=p.admission_id AND kind='launch_identity';
 SELECT * INTO STRICT c FROM factor_cutoff_executions WHERE id=p.cutoff_id;
 SELECT * INTO STRICT ev FROM factor_evaluation_starts WHERE id=c.evaluation_id;
 j:=p.intent; output:=j->'output';
 IF (j - ARRAY['schema_version','journal_id','reservation_id','admission','source_id','generation_start_id','factor_version_id','source_object_id','source_sha256','prefix_sha256','snapshot_input_sha256','cutoff','stop_receipt_id','stop_receipt_sha256','extraction_receipt_id','extraction_receipt_sha256','output','journal_key']) <> '{}'::jsonb
 OR (j->>'schema_version',j->>'journal_id',j->>'reservation_id',j->>'source_id',j->>'source_object_id',j->>'source_sha256',j->>'stop_receipt_id',j->>'stop_receipt_sha256',j->>'extraction_receipt_id',j->>'extraction_receipt_sha256',j->>'journal_key') IS DISTINCT FROM
 ('mineagent.candidate-native-intent.v2',p.id::text,p.reservation_id::text,p.source_id::text,p.source_object_id::text,p.source_sha256,p.stop_receipt_id::text,st.payload_sha256,p.extraction_receipt_id::text,ex.payload_sha256,p.journal_key)
 OR j->'admission' IS DISTINCT FROM li.payload->'admission'
 OR (a.schema_version,a.role,a.ipc_policy,a.ticket_sha256) IS DISTINCT FROM
 ('mineagent.process-admission.v2'::varchar,'factor_cutoff_candidate'::varchar,'none'::varchar,NULL::varchar)
 OR a.logical_operation_id<>c.id OR a.reservation_id=p.reservation_id
 OR mineagent_rd_source_violation(p.source_id) IS NOT NULL
 OR NOT EXISTS (SELECT 1 FROM factor_source_read_witnesses w WHERE w.source_id=p.source_id
   AND w.source_object_id=p.source_object_id AND w.source_sha256=p.source_sha256)
 OR (a.image_id,a.profile) IS DISTINCT FROM (c.projection->>'candidate_image',c.projection->>'candidate_profile')
 OR p.size_bytes>(c.projection->>'max_output_bytes')::bigint
 OR (c.cutoff_ordinal>=0 AND c.cutoff_ordinal<ev.expected_cutoffs) IS NOT TRUE
 OR ((c.projection->>'output_session')::date >= (ev.snapshot_input->'window'->>'start')::date
     AND (c.projection->>'output_session')::date < (ev.snapshot_input->'window'->>'end')::date) IS NOT TRUE
 OR c.projection->>'snapshot_input_sha256' IS DISTINCT FROM ev.input_sha256
 OR NOT EXISTS (SELECT 1 FROM factor_generation_starts gs JOIN factor_research_specs rs ON rs.id=gs.spec_id
   WHERE gs.id=c.start_id AND
    (rs.config->'runtime'->>'candidate_image',rs.config->'runtime'->>'candidate_profile',rs.config->'runtime'->>'normalizer_image',rs.config->'runtime'->>'normalizer_profile') IS NOT DISTINCT FROM
    (c.projection->>'candidate_image',c.projection->>'candidate_profile',c.projection->>'normalizer_image',c.projection->>'normalizer_profile'))

 OR c.projection_sha256<>encode(sha256(convert_to(mineagent_native_metadata(c.projection),'UTF8')),'hex')
 OR ev.input_sha256<>encode(sha256(convert_to(mineagent_native_metadata(ev.snapshot_input),'UTF8')),'hex')
 OR a.input_sha256<>encode(sha256(convert_to(mineagent_native_metadata(li.payload->'inputs'),'UTF8')),'hex')
 OR (li.payload->'admission') IS DISTINCT FROM jsonb_build_object(
   'schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,'run_id',a.run_id,
   'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
   'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,
   'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,
   'image_id',a.image_id,'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',NULL))
 OR ident.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-identity.v2'
 OR ident.payload->>'admission_sha256' IS DISTINCT FROM encode(sha256(convert_to(mineagent_native_metadata(li.payload->'admission'),'UTF8')),'hex')
 OR ident.payload->>'image_id' IS DISTINCT FROM a.image_id
 OR st.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-stop.v2'
 OR ex.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-extraction.v2'
 OR st.payload->'confirmed_absent' IS DISTINCT FROM 'true'::jsonb
 OR st.payload->'kernel_identity_absent' IS DISTINCT FROM 'true'::jsonb
 OR st.payload->'deadline_bound_met' IS DISTINCT FROM 'true'::jsonb
 OR ((st.payload->>'observed_at')::timestamptz >= (li.payload->>'issued_at')::timestamptz
     AND (st.payload->>'observed_at')::timestamptz <= (li.payload->>'not_after')::timestamptz+interval '1 second') IS NOT TRUE

 OR ex.payload->'protected_frame_complete' IS DISTINCT FROM 'true'::jsonb
 OR ex.payload->'producer_quiesced' IS DISTINCT FROM 'true'::jsonb
 OR ex.payload->>'protocol' IS DISTINCT FROM 'MANU2'
 OR ex.payload->'size_bytes' IS DISTINCT FROM to_jsonb(p.size_bytes)
 OR ident.payload->>'container_id' IS NULL OR ident.payload->>'launch_sha256' IS NULL
 OR jsonb_typeof(ident.payload->'root_device') IS DISTINCT FROM 'number'
 OR jsonb_typeof(ident.payload->'root_inode') IS DISTINCT FROM 'number'
 OR (ident.payload->>'root_device') !~ '^[0-9]+$'
 OR (ident.payload->>'root_inode') !~ '^[1-9][0-9]*$'
 OR ident.payload->>'launch_sha256' IS DISTINCT FROM encode(sha256(convert_to(mineagent_native_metadata(
   jsonb_build_object('schema_version','mineagent.numeric-process-launch.v2','intent',li.payload,
     'runtime_name','mineagent-numeric-'||a.id::text,
     'root_device',ident.payload->'root_device','root_inode',ident.payload->'root_inode')),'UTF8')),'hex')

 OR (st.payload->>'container_id',st.payload->>'launch_sha256',st.payload->>'identity_sha256',ex.payload->>'launch_sha256',ex.payload->>'identity_sha256') IS DISTINCT FROM
   (ident.payload->>'container_id',ident.payload->>'launch_sha256',ident.payload_sha256,ident.payload->>'launch_sha256',ident.payload_sha256)
 OR EXISTS (SELECT 1 FROM research_process_receipts rc WHERE rc.admission_id=a.id AND
   rc.payload_sha256<>encode(sha256(convert_to(mineagent_native_metadata(rc.payload),'UTF8')),'hex'))

 OR NOT EXISTS (SELECT 1 FROM research_process_states s WHERE s.admission_id=a.id AND s.status='stopped')
 OR (li.payload->'inputs'->>'source_object_id',li.payload->'inputs'->>'source_sha256',li.payload->'inputs'->>'prefix_sha256') IS DISTINCT FROM
 (p.source_object_id::text,p.source_sha256,c.projection_sha256)
 OR (j->>'generation_start_id',j->>'factor_version_id',j->>'prefix_sha256',j->>'snapshot_input_sha256') IS DISTINCT FROM
 (c.start_id::text,c.factor_version_id::text,c.projection_sha256,ev.input_sha256)
 OR (j->>'cutoff')::timestamptz IS DISTINCT FROM (c.projection->>'host_cutoff')::timestamptz
 OR (li.payload->'inputs'->>'cutoff')::timestamptz IS DISTINCT FROM (j->>'cutoff')::timestamptz
 OR (ex.kind,ex.payload->>'basename',ex.payload->>'sha256',(ex.payload->>'size_bytes')::bigint) IS DISTINCT FROM
 ('extraction'::varchar,'result.h5',p.sha256,p.size_bytes)
 OR st.kind<>'stop'
 OR octet_length(mineagent_native_metadata(j))>65536
 OR encode(sha256(convert_to(mineagent_native_metadata(j),'UTF8')),'hex')<>p.intent_sha256
 OR p.peak_bytes<>2*p.size_bytes+131072
 OR r.purpose<>'object_storage' OR r.amounts->>'storage_bytes' IS DISTINCT FROM p.peak_bytes::text
 OR EXISTS (SELECT 1 FROM jsonb_each(r.amounts) x WHERE x.key<>'storage_bytes' AND x.value<>'0'::jsonb)
 OR r.logical_key<>'native:'||p.id::text
 OR NOT EXISTS (SELECT 1 FROM engine_object_reservation_consumers ec WHERE ec.reservation_id=p.reservation_id AND ec.consumer_kind='native_hdf' AND ec.consumer_id=p.id)
 THEN RAISE EXCEPTION 'Native publication binding differs'; END IF;
 IF output IS DISTINCT FROM jsonb_build_object('schema_version','mineagent.factor-storage-identity.v2',
 'owner_id',p.owner_id,'storage_key',p.storage_key,'object',jsonb_build_object('role','native_hdf','object_id',p.object_id,
 'object_kind','native_hdf','media_type','application/x-hdf5','sha256',p.sha256,'size_bytes',p.size_bytes))
 THEN RAISE EXCEPTION 'Native output identity differs'; END IF;
 SELECT * INTO o FROM factor_native_outcomes WHERE publication_id=p.id;
 IF FOUND THEN
  SELECT * INTO STRICT obj FROM factor_native_objects WHERE id=p.object_id;
  IF o.journal_sha256<>p.intent_sha256 OR o.physical_receipt_sha256<>
    encode(sha256(convert_to(mineagent_native_metadata(jsonb_build_object('storage_key',p.storage_key,'sha256',p.sha256,'size_bytes',p.size_bytes,'journal_key',p.journal_key,'journal_sha256',p.intent_sha256)),'UTF8')),'hex')
   OR NOT EXISTS (SELECT 1 FROM research_budget_settlements bs WHERE bs.reservation_id=r.id AND bs.outcome='conservative_unknown' AND bs.charge=r.amounts)
   OR r.state<>'settled'
  THEN RAISE EXCEPTION 'Native outcome/charge differs'; END IF;
 ELSE
  IF r.state='settled' THEN RAISE EXCEPTION 'Pending native writer cannot be settled'; END IF;
 END IF;
 RETURN;
END $$;

CREATE FUNCTION mineagent_native_publication_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pid uuid;
BEGIN
 IF TG_TABLE_NAME='factor_native_publications' THEN PERFORM mineagent_native_check(NEW.id);
 ELSIF TG_TABLE_NAME='factor_native_outcomes' THEN PERFORM mineagent_native_check(NEW.publication_id);
 ELSIF TG_TABLE_NAME='factor_native_objects' THEN
  SELECT publication_id INTO pid FROM factor_native_outcomes WHERE object_id=NEW.id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Native object requires publication outcome'; END IF;
  PERFORM mineagent_native_check(pid);
 ELSIF TG_TABLE_NAME IN ('research_budget_reservations','research_budget_settlements') THEN
  FOR pid IN SELECT id FROM factor_native_publications WHERE reservation_id=
   (to_jsonb(NEW)->>CASE WHEN TG_TABLE_NAME='research_budget_reservations' THEN 'id' ELSE 'reservation_id' END)::uuid
  LOOP PERFORM mineagent_native_check(pid); END LOOP;
 ELSE RAISE EXCEPTION 'Unknown native evidence'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION mineagent_native_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; aid uuid; moving boolean; ending boolean;
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN
  rid:=NEW.id; aid:=NULL;
  moving:=(NEW.generation,NEW.active_attempt_id) IS DISTINCT FROM (OLD.generation,OLD.active_attempt_id);
  ending:=NEW.status IN ('succeeded','failed','cancelled') AND NEW.status IS DISTINCT FROM OLD.status;
 ELSE
  rid:=NEW.run_id; aid:=NEW.id; moving:=false;
  ending:=(NEW.finished_at IS NOT NULL AND OLD.finished_at IS NULL) OR (NEW.sandbox_stopped AND NOT OLD.sandbox_stopped);
 END IF;
 IF (moving OR ending) AND EXISTS (SELECT 1 FROM factor_native_publications p
   WHERE p.run_id=rid AND (aid IS NULL OR p.attempt_id=aid) AND NOT EXISTS
   (SELECT 1 FROM factor_native_outcomes o WHERE o.publication_id=p.id))
 THEN RAISE EXCEPTION 'Unresolved native publication prevents terminalization or resume'; END IF;
 RETURN NEW;
END $$;
"""

CLAIM_SQL = r"""
ALTER TABLE engine_object_reservation_consumers DROP CONSTRAINT engine_object_reservation_consumers_consumer_kind_check;
ALTER TABLE engine_object_reservation_consumers ADD CONSTRAINT engine_object_reservation_consumers_consumer_kind_check CHECK (consumer_kind IN ('research_object','factor_source','native_hdf'));
CREATE OR REPLACE FUNCTION mineagent_engine_object_claim() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text; rid uuid; previous engine_object_reservation_consumers%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='research_object_publications' THEN kind:='research_object'; rid:=NEW.budget_reservation_id;
 ELSIF TG_TABLE_NAME='factor_source_publications' THEN kind:='factor_source'; rid:=NEW.budget_reservation_id;
 ELSIF TG_TABLE_NAME='factor_native_publications' THEN kind:='native_hdf'; rid:=NEW.reservation_id;
 ELSE RAISE EXCEPTION 'Unknown reservation consumer'; END IF;
 -- The primary key arbitrates before bytes can be written, including concurrent
 -- snapshots. An invisible winner fails closed, never a second physical claim.
 PERFORM 1 FROM research_budget_reservations WHERE id=rid FOR UPDATE;
 BEGIN
  INSERT INTO engine_object_reservation_consumers VALUES
   (rid,NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,kind,NEW.id);
 EXCEPTION WHEN unique_violation THEN
  SELECT * INTO previous FROM engine_object_reservation_consumers WHERE reservation_id=rid;
  IF NOT FOUND OR (previous.owner_id,previous.run_id,previous.attempt_id,previous.generation,previous.consumer_kind,previous.consumer_id)
   IS DISTINCT FROM (NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,kind,NEW.id)
  THEN RAISE EXCEPTION 'Reservation already belongs to another consumer'; END IF;
 END;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION mineagent_engine_object_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; claim engine_object_reservation_consumers%ROWTYPE; n bigint; matching bigint;
BEGIN
 IF TG_TABLE_NAME IN ('engine_object_reservation_consumers','factor_native_publications') THEN rid:=NEW.reservation_id;
 ELSIF TG_TABLE_NAME IN ('research_object_publications','factor_source_publications') THEN rid:=NEW.budget_reservation_id;
 ELSE RAISE EXCEPTION 'Unknown reservation consumer'; END IF;
 SELECT * INTO claim FROM engine_object_reservation_consumers WHERE reservation_id=rid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Reservation consumer claim missing'; END IF;
 SELECT count(*),count(*) FILTER (WHERE (kind,id,owner_id,run_id,attempt_id,generation)=
  (claim.consumer_kind,claim.consumer_id,claim.owner_id,claim.run_id,claim.attempt_id,claim.generation)) INTO n,matching FROM (
  SELECT 'research_object' AS kind,id,owner_id,run_id,attempt_id,generation FROM research_object_publications WHERE budget_reservation_id=rid
  UNION ALL SELECT 'factor_source',id,owner_id,run_id,attempt_id,generation FROM factor_source_publications WHERE budget_reservation_id=rid
  UNION ALL SELECT 'native_hdf',id,owner_id,run_id,attempt_id,generation FROM factor_native_publications WHERE reservation_id=rid
 ) consumers;
 IF n<>1 OR matching<>1 THEN RAISE EXCEPTION 'Reservation requires exactly one matching physical consumer'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER claim_native_reservation BEFORE INSERT ON factor_native_publications FOR EACH ROW EXECUTE FUNCTION mineagent_engine_object_claim();
CREATE CONSTRAINT TRIGGER check_native_consumer AFTER INSERT ON factor_native_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_engine_object_consistent();
"""

NORMALIZER_SQL = r"""
CREATE FUNCTION mineagent_native_normalizer_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p factor_native_publications%ROWTYPE; a research_process_admissions%ROWTYPE;
 li research_process_receipts%ROWTYPE; r research_budget_reservations%ROWTYPE;
 c factor_cutoff_executions%ROWTYPE; listing jsonb; manifest jsonb; files jsonb;
BEGIN
 SELECT * INTO STRICT p FROM factor_native_publications WHERE id=NEW.publication_id;
 PERFORM mineagent_native_check(p.id);
 IF NOT EXISTS(SELECT 1 FROM factor_native_outcomes WHERE publication_id=p.id)
 THEN RAISE EXCEPTION 'Normalizer requires completed native publication'; END IF;
 SELECT * INTO STRICT a FROM research_process_admissions WHERE id=NEW.admission_id;
 SELECT * INTO STRICT r FROM research_budget_reservations WHERE id=NEW.reservation_id;
 SELECT * INTO STRICT li FROM research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 SELECT * INTO STRICT c FROM factor_cutoff_executions WHERE id=p.cutoff_id;
 SELECT value INTO STRICT listing FROM jsonb_array_elements(c.projection->'input_files') WHERE value->>'name'='listings.json';
 manifest:=jsonb_build_object('schema_version','mineagent.factor-normalization.v1',
  'hdf_sha256',p.sha256,'listings_sha256',listing->>'sha256',
  'output_event_ns',(extract(epoch FROM (c.projection->>'output_event_time')::timestamptz)*1000000000)::bigint,
  'max_rows',c.projection->'max_output_rows','max_bytes',c.projection->'max_output_bytes');
 files:=jsonb_build_array(
  jsonb_build_object('destination','/input/listings.json','sha256',listing->>'sha256','size_bytes',listing->'size_bytes'),
  jsonb_build_object('destination','/input/manifest.json','sha256',encode(sha256(convert_to(mineagent_native_metadata(manifest),'UTF8')),'hex'),'size_bytes',octet_length(mineagent_native_metadata(manifest))),
  jsonb_build_object('destination','/input/result.h5','sha256',p.sha256,'size_bytes',p.size_bytes));
 IF (a.schema_version,a.role,a.ipc_policy,a.ticket_sha256,a.profile,a.image_id,a.logical_operation_id,a.reservation_id,a.input_sha256) IS DISTINCT FROM
  ('mineagent.process-admission.v2'::varchar,'factor_normalizer'::varchar,'none'::varchar,NULL::varchar,
   c.projection->>'normalizer_profile',c.projection->>'normalizer_image',c.id,NEW.reservation_id,NEW.input_sha256)
 OR li.payload_sha256<>NEW.intent_sha256
 OR encode(sha256(convert_to(mineagent_native_metadata(li.payload),'UTF8')),'hex')<>NEW.intent_sha256
 OR encode(sha256(convert_to(mineagent_native_metadata(li.payload->'inputs'),'UTF8')),'hex')<>a.input_sha256
 OR (li.payload->'admission') IS DISTINCT FROM jsonb_build_object(
   'schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,'run_id',a.run_id,
   'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
   'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,
   'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,
   'image_id',a.image_id,'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',NULL))
 OR (li.payload->'inputs'->>'source_object_id',li.payload->'inputs'->>'source_sha256',li.payload->'inputs'->>'prefix_sha256') IS DISTINCT FROM
   (p.source_object_id::text,p.source_sha256,c.projection_sha256)
 OR (li.payload->'inputs'->>'cutoff')::timestamptz IS DISTINCT FROM (c.projection->>'host_cutoff')::timestamptz
 OR li.payload->'inputs'->'files' IS DISTINCT FROM files
 OR r.purpose<>'process_unit' OR (r.amounts->>'process_slots')::numeric<1
 OR (r.amounts->>'wall_milliseconds')::numeric<60000 OR (r.amounts->>'storage_bytes')::numeric<67174400
 THEN RAISE EXCEPTION 'Native normalizer admission/input/reservation differs'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_factor_native_normalizer_inputs BEFORE UPDATE OR DELETE ON factor_native_normalizer_inputs FOR EACH ROW EXECUTE FUNCTION mineagent_protect_neutral();
CREATE CONSTRAINT TRIGGER check_native_normalizer AFTER INSERT OR UPDATE ON factor_native_normalizer_inputs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_native_normalizer_consistent();
"""

CANONICAL_SQL = r"""
CREATE FUNCTION mineagent_native_metadata(v jsonb, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
 IF depth<0 OR depth>16 OR octet_length(v::text)>1048576 THEN
  RAISE EXCEPTION 'Native metadata exceeds fixed bound'; END IF;
 RETURN mineagent_rd_canonical_json(v,true,depth);
END $$;
"""


QUALIFIED_IDENTIFIERS = (
    "engine_object_reservation_consumers",
    "factor_cutoff_executions",
    "factor_evaluation_starts",
    "factor_generation_sources",
    "factor_generation_starts",
    "factor_native_normalizer_inputs",
    "factor_native_objects",
    "factor_native_outcomes",
    "factor_native_publications",
    "factor_objects",
    "factor_research_specs",
    "factor_source_publications",
    "factor_source_read_witnesses",
    "mineagent_engine_object_claim",
    "mineagent_engine_object_consistent",
    "mineagent_native_check",
    "mineagent_native_metadata",
    "mineagent_native_normalizer_consistent",
    "mineagent_native_publication_consistent",
    "mineagent_native_run_guard",
    "mineagent_protect_neutral",
    "mineagent_rd_canonical_json",
    "mineagent_rd_source_violation",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_object_publications",
    "research_process_admissions",
    "research_process_receipts",
    "research_process_states",
)


def qualified_sql(sql):
    import re

    pattern = re.compile(r"\b(" + "|".join(QUALIFIED_IDENTIFIERS) + r")\b")
    parts = re.split(r"('(?:''|[^'])*')", sql)
    result = "".join(
        part if index % 2 else pattern.sub(r"@SCHEMA@.\1", part)
        for index, part in enumerate(parts)
    )
    return re.sub(
        r"(LANGUAGE plpgsql(?: IMMUTABLE STRICT)?)( AS \$\$)",
        r"\1 SET search_path=pg_catalog\2",
        result,
    )
