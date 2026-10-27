"""Explicit successor predicates. Frozen migration must copy, never import these.

Metadata SQL supplements fresh physical resolution; it cannot inspect a native
file or turn an arbitrary DB payload into actual execution proof.
"""

NORMALIZER_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_execution_metadata_sha(v jsonb)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(v,true),'UTF8')),'hex')
$$;
CREATE FUNCTION @SCHEMA@.mineagent_execution_normalizer_check(aid uuid, part jsonb)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a @SCHEMA@.research_process_admissions%ROWTYPE;
 li @SCHEMA@.research_process_receipts%ROWTYPE;
 ident @SCHEMA@.research_process_receipts%ROWTYPE;
 st @SCHEMA@.research_process_receipts%ROWTYPE;
 ex @SCHEMA@.research_process_receipts%ROWTYPE;
 claim @SCHEMA@.factor_native_normalizer_inputs%ROWTYPE;
 native @SCHEMA@.factor_native_publications%ROWTYPE;
 r @SCHEMA@.research_budget_reservations%ROWTYPE;
 launch jsonb; input_manifest jsonb; listings jsonb; bound jsonb;
BEGIN
 SELECT * INTO STRICT a FROM @SCHEMA@.research_process_admissions WHERE id=aid;
 SELECT * INTO STRICT claim FROM @SCHEMA@.factor_native_normalizer_inputs WHERE admission_id=aid;
 SELECT * INTO STRICT native FROM @SCHEMA@.factor_native_publications WHERE id=claim.publication_id;
 PERFORM @SCHEMA@.mineagent_native_check(native.id);
 IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.factor_native_outcomes WHERE publication_id=native.id)
 THEN RAISE EXCEPTION 'Snapshot normalizer requires completed native input'; END IF;
 SELECT * INTO STRICT li FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid AND kind='launch_intent';
 SELECT * INTO STRICT ident FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid AND kind='launch_identity';
 SELECT * INTO STRICT st FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid AND kind='stop';
 SELECT * INTO STRICT ex FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid AND kind='extraction';
 SELECT * INTO STRICT r FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 -- Reject permissive PostgreSQL text casts before evaluating linked hashes.
 IF jsonb_typeof(li.payload) IS DISTINCT FROM 'object'
 OR NOT li.payload ?& ARRAY['schema_version','id','admission','inputs','issued_at','not_after']
 OR (li.payload-ARRAY['schema_version','id','admission','inputs','issued_at','not_after'])<>'{}'::jsonb
 OR li.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-intent.v2'
 OR jsonb_typeof(li.payload->'id') IS DISTINCT FROM 'string'
 OR (li.payload->>'id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') IS NOT TRUE
 OR jsonb_typeof(li.payload->'inputs') IS DISTINCT FROM 'object'
 OR NOT (li.payload->'inputs') ?& ARRAY['schema_version','source_object_id','source_sha256','prefix_sha256','cutoff','files']
 OR ((li.payload->'inputs')-ARRAY['schema_version','source_object_id','source_sha256','prefix_sha256','cutoff','files'])<>'{}'::jsonb
 OR li.payload->'inputs'->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-input.v2'
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['source_object_id','source_sha256','prefix_sha256','cutoff']) k WHERE jsonb_typeof(li.payload->'inputs'->k) IS DISTINCT FROM 'string')
 OR jsonb_typeof(li.payload->'inputs'->'files') IS DISTINCT FROM 'array'
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['admission_sha256','launch_sha256','container_id','image_id']) k WHERE jsonb_typeof(ident.payload->k) IS DISTINCT FROM 'string')
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['launch_sha256','identity_sha256','container_id']) k WHERE jsonb_typeof(st.payload->k) IS DISTINCT FROM 'string')
 OR jsonb_typeof(ident.payload) IS DISTINCT FROM 'object'
 OR NOT ident.payload ?& ARRAY['schema_version','admission_sha256','launch_sha256','container_id','image_id','root_device','root_inode','init_pid','init_start_ticks','boot_id','pid_namespace_inode']
 OR (ident.payload-ARRAY['schema_version','admission_sha256','launch_sha256','container_id','image_id','root_device','root_inode','init_pid','init_start_ticks','boot_id','pid_namespace_inode'])<>'{}'::jsonb
 OR jsonb_typeof(st.payload) IS DISTINCT FROM 'object'
 OR NOT st.payload ?& ARRAY['schema_version','launch_sha256','identity_sha256','container_id','confirmed_absent','kernel_identity_absent','observed_at','deadline_bound_met']
 OR (st.payload-ARRAY['schema_version','launch_sha256','identity_sha256','container_id','confirmed_absent','kernel_identity_absent','observed_at','deadline_bound_met'])<>'{}'::jsonb
 OR jsonb_typeof(ex.payload) IS DISTINCT FROM 'object'
 OR NOT ex.payload ?& ARRAY['schema_version','launch_sha256','identity_sha256','protocol','basename','sha256','size_bytes','protected_frame_complete','producer_quiesced','report_basename','report_sha256','report_size_bytes','input_manifest_sha256','listings_sha256']
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['root_device','root_inode','init_pid','init_start_ticks','pid_namespace_inode']) k
   WHERE jsonb_typeof(ident.payload->k) IS DISTINCT FROM 'number'
   OR ((ident.payload->>k) ~ '^[0-9]+$') IS NOT TRUE)
 OR jsonb_typeof(ident.payload->'boot_id') IS DISTINCT FROM 'string'
 OR (ident.payload->>'boot_id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') IS NOT TRUE
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['size_bytes','report_size_bytes']) k
   WHERE jsonb_typeof(ex.payload->k) IS DISTINCT FROM 'number'
   OR ((ex.payload->>k) ~ '^[1-9][0-9]*$') IS NOT TRUE)
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['launch_sha256','identity_sha256','sha256','report_sha256','input_manifest_sha256','listings_sha256']) k
   WHERE jsonb_typeof(ex.payload->k) IS DISTINCT FROM 'string'
   OR ((ex.payload->>k) ~ '^[a-f0-9]{64}$') IS NOT TRUE)
 OR EXISTS(SELECT 1 FROM (VALUES(li.payload->'issued_at'),(li.payload->'not_after'),(st.payload->'observed_at'),(li.payload->'inputs'->'cutoff')) t(v)
   WHERE jsonb_typeof(v) IS DISTINCT FROM 'string'
   OR ((v #>> '{}') ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|\+00:00)$') IS NOT TRUE)
 THEN RAISE EXCEPTION 'Snapshot normalizer records are not closed typed UTC records'; END IF;
 IF ((li.payload->>'not_after')::timestamptz-(li.payload->>'issued_at')::timestamptz>interval '0 seconds'
 AND (li.payload->>'not_after')::timestamptz-(li.payload->>'issued_at')::timestamptz<=interval '10 seconds') IS NOT TRUE
 THEN RAISE EXCEPTION 'Snapshot normalizer intent exceeds deadline profile'; END IF;
 IF (a.schema_version,a.role,a.ipc_policy,a.ticket_sha256) IS DISTINCT FROM
 ('mineagent.process-admission.v2'::varchar,'factor_normalizer'::varchar,'none'::varchar,NULL::varchar)
 OR (SELECT count(*) FROM @SCHEMA@.research_process_receipts WHERE admission_id=aid)<>4
 OR a.logical_operation_id<>native.cutoff_id OR a.reservation_id<>claim.reservation_id
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_process_states WHERE admission_id=aid AND status='stopped')
 OR a.input_sha256<>claim.input_sha256 OR li.payload_sha256<>claim.intent_sha256
 OR a.input_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(li.payload->'inputs')
 OR li.payload_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(li.payload)
 OR ident.payload_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(ident.payload)
 OR st.payload_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(st.payload)
 OR ex.payload_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(ex.payload)
 OR li.payload->'admission' IS DISTINCT FROM jsonb_build_object(
 'schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,'run_id',a.run_id,
 'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,
 'logical_operation_id',a.logical_operation_id,'launch_ordinal',a.launch_ordinal,
 'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,
 'image_id',a.image_id,'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',NULL))
 OR ident.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-identity.v2'
 OR ident.payload->>'admission_sha256' IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(li.payload->'admission')
 OR ident.payload->>'image_id' IS DISTINCT FROM a.image_id
 OR (ident.payload->>'container_id' ~ '^[a-f0-9]{64}$') IS NOT TRUE
 OR (ident.payload->>'root_device' ~ '^[0-9]+$') IS NOT TRUE
 OR (ident.payload->>'root_inode' ~ '^[1-9][0-9]*$') IS NOT TRUE
 OR (ident.payload->>'init_pid' ~ '^[1-9][0-9]*$') IS NOT TRUE
 OR (ident.payload->>'init_start_ticks' ~ '^[1-9][0-9]*$') IS NOT TRUE
 OR (ident.payload->>'pid_namespace_inode' ~ '^[1-9][0-9]*$') IS NOT TRUE
 OR ident.payload->>'boot_id' IS NULL
 THEN RAISE EXCEPTION 'Snapshot normalizer admission/identity differs'; END IF;
 launch:=jsonb_build_object('schema_version','mineagent.numeric-process-launch.v2',
 'intent',li.payload,'runtime_name','mineagent-numeric-'||aid::text,
 'root_device',ident.payload->'root_device','root_inode',ident.payload->'root_inode');
 IF ident.payload->>'launch_sha256' IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(launch)
 OR st.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-stop.v2'
 OR st.payload->>'launch_sha256' IS DISTINCT FROM ident.payload->>'launch_sha256'
 OR st.payload->>'identity_sha256' IS DISTINCT FROM ident.payload_sha256
 OR st.payload->>'container_id' IS DISTINCT FROM ident.payload->>'container_id'
 OR st.payload->'confirmed_absent' IS DISTINCT FROM 'true'::jsonb
 OR st.payload->'kernel_identity_absent' IS DISTINCT FROM 'true'::jsonb
 OR st.payload->'deadline_bound_met' IS DISTINCT FROM 'true'::jsonb
 OR ((st.payload->>'observed_at')::timestamptz >= (li.payload->>'issued_at')::timestamptz
 AND (st.payload->>'observed_at')::timestamptz <= (li.payload->>'not_after')::timestamptz+interval '1 second') IS NOT TRUE
 OR ex.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-extraction.v3'
 OR (ex.payload - ARRAY['schema_version','launch_sha256','identity_sha256','protocol','basename','sha256','size_bytes','protected_frame_complete','producer_quiesced','report_basename','report_sha256','report_size_bytes','input_manifest_sha256','listings_sha256'])<>'{}'::jsonb
 OR ex.payload->>'protocol' IS DISTINCT FROM 'MANU2-report-v1'
 OR ex.payload->>'basename' IS DISTINCT FROM 'values.parquet'
 OR ex.payload->>'report_basename' IS DISTINCT FROM 'rows.json'
 OR ex.payload->>'launch_sha256' IS DISTINCT FROM ident.payload->>'launch_sha256'
 OR ex.payload->>'identity_sha256' IS DISTINCT FROM ident.payload_sha256
 OR ex.payload->'protected_frame_complete' IS DISTINCT FROM 'true'::jsonb
 OR ex.payload->'producer_quiesced' IS DISTINCT FROM 'true'::jsonb
 OR ((ex.payload->>'size_bytes')::bigint>0 AND (ex.payload->>'report_size_bytes')::bigint>0
 AND (ex.payload->>'size_bytes')::bigint+(ex.payload->>'report_size_bytes')::bigint<=16777216) IS NOT TRUE
 THEN RAISE EXCEPTION 'Snapshot normalizer stopped protected output differs'; END IF;
 SELECT projection INTO STRICT bound FROM @SCHEMA@.factor_cutoff_executions WHERE id=native.cutoff_id;
 SELECT value INTO STRICT listings FROM jsonb_array_elements(bound->'input_files') WHERE value->>'name'='listings.json';
 input_manifest:=jsonb_build_object('schema_version','mineagent.factor-normalization.v2',
 'hdf_sha256',native.sha256,'listings_sha256',listings->>'sha256',
 'output_event_ns',(extract(epoch FROM (bound->>'output_event_time')::timestamptz)*1000000000)::bigint,
 'max_rows',bound->'max_output_rows','max_bytes',bound->'max_output_bytes');
 IF (ex.payload->>'input_manifest_sha256',ex.payload->>'listings_sha256') IS DISTINCT FROM
 (@SCHEMA@.mineagent_execution_metadata_sha(input_manifest),listings->>'sha256')
 OR (part->>'normalizer_admission_id',part->>'native_publication_id',part->>'candidate_admission_id',
 part->>'cutoff_id',part->>'normalizer_input_sha256',part->>'stop_receipt_id',part->>'stop_receipt_sha256',
 part->>'extraction_receipt_id',part->>'extraction_receipt_sha256',part->>'primitive_report_sha256') IS DISTINCT FROM
 (aid::text,native.id::text,native.admission_id::text,native.cutoff_id::text,a.input_sha256,
 st.id::text,st.payload_sha256,ex.id::text,ex.payload_sha256,ex.payload->>'report_sha256')
 OR part->'primitive_report_size_bytes' IS DISTINCT FROM ex.payload->'report_size_bytes'
 OR part->'values'->>'sha256' IS DISTINCT FROM ex.payload->>'sha256'
 OR part->'values'->'size_bytes' IS DISTINCT FROM ex.payload->'size_bytes'
 OR part->>'prefix_sha256' IS DISTINCT FROM native.intent->>'prefix_sha256'
 OR r.purpose<>'process_unit' OR (r.amounts->>'process_slots')::numeric<1
 OR (r.amounts->>'wall_milliseconds')::numeric<60000 OR (r.amounts->>'storage_bytes')::numeric<67174400
 OR (r.state NOT IN ('reserved','uncertain') AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements bs
 WHERE bs.reservation_id=r.id AND r.state='settled' AND bs.outcome='conservative_unknown' AND bs.charge=r.amounts))
 THEN RAISE EXCEPTION 'Snapshot normalizer extraction/input/reservation binding differs'; END IF;
 RETURN;
END $$;
"""

SNAPSHOT_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_execution_snapshot_check(pid uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE p @SCHEMA@.factor_execution_snapshot_publications%ROWTYPE;
 e @SCHEMA@.factor_evaluation_starts%ROWTYPE;
 r @SCHEMA@.research_budget_reservations%ROWTYPE;
 o @SCHEMA@.factor_execution_snapshot_outcomes%ROWTYPE;
 part @SCHEMA@.factor_execution_snapshot_partitions%ROWTYPE;
 m jsonb; j jsonb; expected jsonb; obj jsonb; objects jsonb;
 n bigint; total bigint; valid bigint; bytes bigint; entries bigint;
BEGIN
 SELECT * INTO STRICT p FROM @SCHEMA@.factor_execution_snapshot_publications WHERE id=pid;
 SELECT * INTO STRICT e FROM @SCHEMA@.factor_evaluation_starts WHERE id=p.evaluation_id;
 SELECT * INTO STRICT r FROM @SCHEMA@.research_budget_reservations WHERE id=p.reservation_id;
 m:=p.manifest; j:=p.journal;
 IF (m->>'schema_version',m->>'snapshot_id',m->>'owner_id',m->>'run_id',m->>'attempt_id',
 m->>'generation',m->>'generation_start_id',m->>'source_id',m->>'evaluation_id',m->>'input_sha256',m->>'dependency_claim') IS DISTINCT FROM
 ('mineagent.factor-execution-snapshot.v1',p.id::text,p.owner_id::text,p.run_id::text,p.attempt_id::text,
 p.generation::text,p.start_id::text,p.source_id::text,p.evaluation_id::text,e.input_sha256,'provided_not_proven_read')
 OR (e.owner_id,e.run_id,e.attempt_id,e.generation,e.start_id,e.evaluated_source_id,e.factor_version_id) IS DISTINCT FROM
 (p.owner_id,p.run_id,p.attempt_id,p.generation,p.start_id,p.source_id,p.factor_version_id)
 OR (m - ARRAY['schema_version','snapshot_id','owner_id','run_id','attempt_id','generation','generation_start_id','source_id','evaluation_id','source_read_witness_id','source_read_witness_sha256','input','input_sha256','expected_cutoffs','partitions','assembled_rows','output_rows_sha256','row_count','valid_count','dependency_claim'])<>'{}'::jsonb
 OR NOT m ?& ARRAY['schema_version','snapshot_id','owner_id','run_id','attempt_id','generation','generation_start_id','source_id','evaluation_id','source_read_witness_id','source_read_witness_sha256','input','input_sha256','expected_cutoffs','partitions','assembled_rows','output_rows_sha256','row_count','valid_count','dependency_claim']
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['generation','expected_cutoffs','row_count','valid_count']) k WHERE jsonb_typeof(m->k) IS DISTINCT FROM 'number' OR ((m->>k) ~ '^[0-9]+$') IS NOT TRUE)
 OR (m->>'output_rows_sha256' ~ '^[a-f0-9]{64}$') IS NOT TRUE
 OR m->'assembled_rows'->>'kind' IS DISTINCT FROM 'assembled_rows'
 OR (j - ARRAY['schema_version','publication_id','reservation_id','manifest','manifest_object','peak_bytes','peak_entries'])<>'{}'::jsonb
 OR m->'input' IS DISTINCT FROM e.snapshot_input
 OR m->>'input_sha256' IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(m->'input')
 OR @SCHEMA@.mineagent_rd_source_violation(p.source_id) IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.factor_source_read_witnesses w WHERE w.source_id=p.source_id
   AND w.id::text=m->>'source_read_witness_id' AND w.witness_sha256=m->>'source_read_witness_sha256')
 OR (m->>'expected_cutoffs')::integer IS DISTINCT FROM e.expected_cutoffs
 OR e.expected_cutoffs NOT BETWEEN 1 AND 64
 OR jsonb_array_length(m->'partitions')<>e.expected_cutoffs
 OR (SELECT count(*) FROM @SCHEMA@.factor_cutoff_executions WHERE evaluation_id=e.id)<>e.expected_cutoffs
 OR @SCHEMA@.mineagent_execution_metadata_sha(m)<>p.manifest_sha256
 OR octet_length(@SCHEMA@.mineagent_rd_canonical_json(m,true))<>p.manifest_size_bytes
 OR j->'manifest' IS DISTINCT FROM m
 OR (j->>'schema_version',j->>'publication_id',j->>'reservation_id') IS DISTINCT FROM
 ('mineagent.execution-snapshot-publication.v1',p.id::text,p.reservation_id::text)
 OR @SCHEMA@.mineagent_execution_metadata_sha(j)<>p.journal_sha256
 OR j->'manifest_object' IS DISTINCT FROM jsonb_build_object('object_id',p.manifest_object_id,'owner_id',p.owner_id,
 'kind','execution_manifest','sha256',p.manifest_sha256,'size_bytes',p.manifest_size_bytes,'storage_key',p.manifest_storage_key)
 OR p.journal_key<>'factors/'||p.owner_id::text||'/objects/'||p.journal_sha256||'.json'
 THEN RAISE EXCEPTION 'Execution snapshot source/input/manifest binding differs'; END IF;
 n:=0; total:=0; valid:=0;
 FOR part IN SELECT * FROM @SCHEMA@.factor_execution_snapshot_partitions WHERE publication_id=p.id ORDER BY ordinal LOOP
  expected:=m->'partitions'->part.ordinal;
  IF part.ordinal<>n OR part.binding IS DISTINCT FROM expected OR part.binding_sha256<>@SCHEMA@.mineagent_execution_metadata_sha(expected)
  OR (part.owner_id,part.run_id,part.attempt_id,part.generation) IS DISTINCT FROM (p.owner_id,p.run_id,p.attempt_id,p.generation)
  OR (expected->>'cutoff_id',expected->>'native_publication_id',expected->>'normalizer_admission_id',
      expected->>'stop_receipt_id',expected->>'extraction_receipt_id') IS DISTINCT FROM
   (part.cutoff_id::text,part.native_publication_id::text,part.normalizer_admission_id::text,part.stop_receipt_id::text,part.extraction_receipt_id::text)
  OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.factor_cutoff_executions c WHERE c.id=part.cutoff_id AND c.evaluation_id=e.id
    AND c.cutoff_ordinal=part.ordinal AND c.evaluated_source_id=p.source_id AND c.start_id=p.start_id
    AND c.projection_sha256=expected->>'prefix_sha256')
  OR NOT expected ?& ARRAY['cutoff_id','ordinal','prefix_sha256','native_publication_id','candidate_admission_id','normalizer_admission_id','normalizer_input_sha256','stop_receipt_id','stop_receipt_sha256','extraction_receipt_id','extraction_receipt_sha256','primitive_report_sha256','primitive_report_size_bytes','values','assembly_sha256','output_rows_sha256','row_count','valid_count']
  OR (expected-ARRAY['cutoff_id','ordinal','prefix_sha256','native_publication_id','candidate_admission_id','normalizer_admission_id','normalizer_input_sha256','stop_receipt_id','stop_receipt_sha256','extraction_receipt_id','extraction_receipt_sha256','primitive_report_sha256','primitive_report_size_bytes','values','assembly_sha256','output_rows_sha256','row_count','valid_count'])<>'{}'::jsonb
  OR expected->'ordinal' IS DISTINCT FROM to_jsonb(part.ordinal)
  OR EXISTS(SELECT 1 FROM unnest(ARRAY['row_count','valid_count','primitive_report_size_bytes']) k WHERE jsonb_typeof(expected->k) IS DISTINCT FROM 'number' OR ((expected->>k) ~ '^[0-9]+$') IS NOT TRUE)
  OR EXISTS(SELECT 1 FROM unnest(ARRAY['assembly_sha256','output_rows_sha256']) k WHERE jsonb_typeof(expected->k) IS DISTINCT FROM 'string' OR ((expected->>k) ~ '^[a-f0-9]{64}$') IS NOT TRUE)
  OR expected->'values'->>'kind' IS DISTINCT FROM 'normalized_values'
  OR ((expected->>'row_count')::integer>0 AND (expected->>'row_count')::integer<=10000
    AND (expected->>'valid_count')::integer>=0 AND (expected->>'valid_count')::integer<=(expected->>'row_count')::integer) IS NOT TRUE
  THEN RAISE EXCEPTION 'Execution snapshot cutoff inventory differs'; END IF;
  PERFORM @SCHEMA@.mineagent_execution_normalizer_check(part.normalizer_admission_id,expected);
  n:=n+1; total:=total+(expected->>'row_count')::bigint; valid:=valid+(expected->>'valid_count')::bigint;
 END LOOP;
 IF n<>e.expected_cutoffs OR total NOT BETWEEN 1 AND 100000 OR total<>(m->>'row_count')::bigint OR valid<>(m->>'valid_count')::bigint
 THEN RAISE EXCEPTION 'Execution snapshot whole evaluation is incomplete'; END IF;
 SELECT jsonb_build_array(j->'manifest_object',m->'assembled_rows')||coalesce(jsonb_agg(value->'values' ORDER BY ord),'[]'::jsonb)
 INTO objects FROM jsonb_array_elements(m->'partitions') WITH ORDINALITY x(value,ord);
 SELECT sum((value->>'size_bytes')::bigint),count(*),count(DISTINCT value->>'object_id') INTO bytes,entries,n FROM jsonb_array_elements(objects);
 IF bytes NOT BETWEEN 1 AND 67108864 OR n<>entries OR p.peak_bytes<>2*bytes+4194304 OR p.peak_entries<>2*(entries+1)+3
 OR j->'peak_bytes' IS DISTINCT FROM to_jsonb(p.peak_bytes) OR j->'peak_entries' IS DISTINCT FROM to_jsonb(p.peak_entries)
 OR r.purpose<>'object_storage' OR r.logical_key<>'execution-snapshot:'||p.id::text
 OR r.amounts->>'storage_bytes' IS DISTINCT FROM p.peak_bytes::text
 OR EXISTS(SELECT 1 FROM jsonb_each(r.amounts) x WHERE x.key<>'storage_bytes' AND x.value<>'0'::jsonb)
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.engine_object_reservation_consumers c WHERE c.reservation_id=p.reservation_id
 AND c.consumer_kind='execution_snapshot' AND c.consumer_id=p.id)
 THEN RAISE EXCEPTION 'Execution snapshot physical inventory/reservation differs'; END IF;
 FOR obj IN SELECT value FROM jsonb_array_elements(objects) LOOP
  IF obj->>'owner_id' IS DISTINCT FROM p.owner_id::text
  OR NOT obj ?& ARRAY['object_id','owner_id','kind','sha256','size_bytes','storage_key']
  OR (obj-ARRAY['object_id','owner_id','kind','sha256','size_bytes','storage_key'])<>'{}'::jsonb
  OR jsonb_typeof(obj->'size_bytes') IS DISTINCT FROM 'number'
  OR (obj->>'size_bytes' ~ '^[1-9][0-9]*$') IS NOT TRUE
  OR obj->>'kind' NOT IN ('normalized_values','assembled_rows','execution_manifest')
  OR ((obj->>'size_bytes')::bigint BETWEEN 1 AND CASE WHEN obj->>'kind'='execution_manifest' THEN 1048576 ELSE 16777216 END) IS NOT TRUE
  OR (obj->>'sha256' ~ '^[a-f0-9]{64}$') IS NOT TRUE
  OR obj->>'storage_key' IS DISTINCT FROM 'factors/'||p.owner_id::text||'/objects/'||
    (obj->>'sha256')||CASE WHEN obj->>'kind'='normalized_values' THEN '.parquet' ELSE '.json' END
  THEN RAISE EXCEPTION 'Execution snapshot object identity differs'; END IF;
 END LOOP;
 SELECT * INTO o FROM @SCHEMA@.factor_execution_snapshot_outcomes WHERE publication_id=p.id;
 IF FOUND THEN
  IF (o.manifest_sha256,o.input_sha256,o.output_sha256,o.physical_receipt_sha256) IS DISTINCT FROM
   (p.manifest_sha256,m->>'input_sha256',m->>'output_rows_sha256',
    @SCHEMA@.mineagent_execution_metadata_sha(jsonb_build_object('journal_sha256',p.journal_sha256,'objects',objects)))
  OR r.state<>'settled' OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements s WHERE s.reservation_id=r.id
      AND s.outcome='conservative_unknown' AND s.charge=r.amounts)
  OR (SELECT count(*) FROM @SCHEMA@.factor_execution_snapshot_objects WHERE publication_id=p.id)<>entries
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(objects) x WHERE NOT EXISTS(
    SELECT 1 FROM @SCHEMA@.factor_execution_snapshot_objects z WHERE z.publication_id=p.id AND
    (z.id::text,z.owner_id::text,z.object_kind,z.sha256,z.size_bytes::text,z.storage_key) IS NOT DISTINCT FROM
    (x.value->>'object_id',x.value->>'owner_id',x.value->>'kind',x.value->>'sha256',x.value->>'size_bytes',x.value->>'storage_key')))
  THEN RAISE EXCEPTION 'Execution snapshot completed outcome/physical charge differs'; END IF;
 ELSE
  IF r.state='settled' OR EXISTS(SELECT 1 FROM @SCHEMA@.factor_execution_snapshot_objects WHERE publication_id=p.id)
  THEN RAISE EXCEPTION 'Pending execution snapshot cannot settle or register objects'; END IF;
 END IF;
 RETURN;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_execution_snapshot_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE pid uuid;
BEGIN
 IF TG_TABLE_NAME='factor_execution_snapshot_publications' THEN pid:=NEW.id;
 ELSIF TG_TABLE_NAME IN ('factor_execution_snapshot_partitions','factor_execution_snapshot_objects','factor_execution_snapshot_outcomes') THEN pid:=NEW.publication_id;
 ELSIF TG_TABLE_NAME IN ('research_budget_reservations','research_budget_settlements') THEN
  FOR pid IN SELECT p.id FROM @SCHEMA@.factor_execution_snapshot_publications p WHERE p.reservation_id=
  (to_jsonb(NEW)->>CASE WHEN TG_TABLE_NAME='research_budget_reservations' THEN 'id' ELSE 'reservation_id' END)::uuid
  UNION SELECT q.publication_id FROM @SCHEMA@.factor_execution_snapshot_partitions q JOIN @SCHEMA@.research_process_admissions a
  ON a.id=q.normalizer_admission_id WHERE a.reservation_id=
  (to_jsonb(NEW)->>CASE WHEN TG_TABLE_NAME='research_budget_reservations' THEN 'id' ELSE 'reservation_id' END)::uuid LOOP
   PERFORM @SCHEMA@.mineagent_execution_snapshot_check(pid);
  END LOOP;
  RETURN NEW;
 ELSIF TG_TABLE_NAME IN ('research_process_admissions','research_process_states','research_process_receipts') THEN
  FOR pid IN SELECT publication_id FROM @SCHEMA@.factor_execution_snapshot_partitions WHERE normalizer_admission_id=
  (to_jsonb(NEW)->>CASE WHEN TG_TABLE_NAME='research_process_admissions' THEN 'id' ELSE 'admission_id' END)::uuid LOOP
   PERFORM @SCHEMA@.mineagent_execution_snapshot_check(pid);
  END LOOP;
  RETURN NEW;
 ELSE RAISE EXCEPTION 'Unknown execution snapshot evidence'; END IF;
 PERFORM @SCHEMA@.mineagent_execution_snapshot_check(pid);
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_execution_snapshot_terminal() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rid uuid; aid uuid; ending boolean; moving boolean;
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN rid:=NEW.id; aid:=NULL;
  moving:=(NEW.generation,NEW.active_attempt_id) IS DISTINCT FROM (OLD.generation,OLD.active_attempt_id);
  ending:=NEW.status IN ('succeeded','failed','cancelled') AND NEW.status IS DISTINCT FROM OLD.status;
 ELSE rid:=NEW.run_id; aid:=NEW.id; moving:=false;
  ending:=(NEW.finished_at IS NOT NULL AND OLD.finished_at IS NULL) OR (NEW.sandbox_stopped AND NOT OLD.sandbox_stopped);
 END IF;
 IF (ending OR moving) AND EXISTS(SELECT 1 FROM @SCHEMA@.factor_execution_snapshot_publications p WHERE p.run_id=rid
 AND (aid IS NULL OR p.attempt_id=aid) AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.factor_execution_snapshot_outcomes o WHERE o.publication_id=p.id))
 THEN RAISE EXCEPTION 'Unresolved execution snapshot prevents terminalization or resume'; END IF;
 RETURN NEW;
END $$;
"""


def shared_claim_sql():
    from backend.app.native_storage_ddl import CLAIM_SQL, qualified_sql

    source = qualified_sql(
        CLAIM_SQL.split("CREATE TRIGGER claim_native_reservation", 1)[0]
    )
    source = source.replace(
        "('research_object','factor_source','native_hdf')",
        "('research_object','factor_source','native_hdf','execution_snapshot')",
    )
    source = source.replace(
        " ELSIF TG_TABLE_NAME='factor_native_publications' THEN kind:='native_hdf'; rid:=NEW.reservation_id;",
        " ELSIF TG_TABLE_NAME='factor_native_publications' THEN kind:='native_hdf'; rid:=NEW.reservation_id;\n ELSIF TG_TABLE_NAME='factor_execution_snapshot_publications' THEN kind:='execution_snapshot'; rid:=NEW.reservation_id;",
    )
    source = source.replace(
        "('engine_object_reservation_consumers','factor_native_publications')",
        "('engine_object_reservation_consumers','factor_native_publications','factor_execution_snapshot_publications')",
    )
    source = source.replace(
        " ) consumers;",
        "  UNION ALL SELECT 'execution_snapshot',id,owner_id,run_id,attempt_id,generation FROM @SCHEMA@.factor_execution_snapshot_publications WHERE reservation_id=rid\n ) consumers;",
    )
    return (
        source
        + """
CREATE TRIGGER claim_execution_snapshot_reservation BEFORE INSERT ON @SCHEMA@.factor_execution_snapshot_publications
FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_engine_object_claim();
CREATE CONSTRAINT TRIGGER check_execution_snapshot_consumer AFTER INSERT ON @SCHEMA@.factor_execution_snapshot_publications
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_engine_object_consistent();
"""
    )


EVALUATION_CLAIM_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_execution_evaluation_claim() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE kind text;
BEGIN
 IF TG_TABLE_NAME='factor_evaluation_outcomes' THEN kind:='legacy_result';
 ELSIF TG_TABLE_NAME='factor_execution_evaluation_outcomes' THEN kind:='execution_result';
 ELSE RAISE EXCEPTION 'Unknown evaluation result kind'; END IF;
 PERFORM 1 FROM @SCHEMA@.factor_evaluation_starts WHERE id=NEW.evaluation_id FOR UPDATE;
 INSERT INTO @SCHEMA@.factor_evaluation_result_consumers(evaluation_id,owner_id,run_id,attempt_id,generation,consumer_kind,consumer_id)
 VALUES(NEW.evaluation_id,NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,kind,NEW.id);
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_execution_evaluation_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE eid uuid; claim @SCHEMA@.factor_evaluation_result_consumers%ROWTYPE; n bigint; matching bigint;
BEGIN
 eid:=NEW.evaluation_id;
 SELECT * INTO STRICT claim FROM @SCHEMA@.factor_evaluation_result_consumers WHERE evaluation_id=eid;
 SELECT count(*),count(*) FILTER(WHERE (kind,id,owner_id,run_id,attempt_id,generation) IS NOT DISTINCT FROM
 (claim.consumer_kind,claim.consumer_id,claim.owner_id,claim.run_id,claim.attempt_id,claim.generation)) INTO n,matching FROM (
  SELECT 'legacy_result' AS kind,id,owner_id,run_id,attempt_id,generation FROM @SCHEMA@.factor_evaluation_outcomes WHERE evaluation_id=eid
  UNION ALL SELECT 'execution_result',id,owner_id,run_id,attempt_id,generation FROM @SCHEMA@.factor_execution_evaluation_outcomes WHERE evaluation_id=eid
 ) consumers;
 IF n<>1 OR matching<>1 THEN RAISE EXCEPTION 'Evaluation requires exactly one scoped result'; END IF;
 RETURN NEW;
END $$;
"""

EVALUATION_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_execution_evaluation_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE p @SCHEMA@.factor_execution_snapshot_publications%ROWTYPE;
 o @SCHEMA@.factor_execution_snapshot_outcomes%ROWTYPE;
 b @SCHEMA@.factor_generation_boundaries%ROWTYPE;
BEGIN
 SELECT * INTO STRICT p FROM @SCHEMA@.factor_execution_snapshot_publications WHERE id=NEW.snapshot_id;
 SELECT * INTO STRICT o FROM @SCHEMA@.factor_execution_snapshot_outcomes WHERE publication_id=p.id;
 SELECT * INTO STRICT b FROM @SCHEMA@.factor_generation_boundaries WHERE id=NEW.boundary_id;
 PERFORM @SCHEMA@.mineagent_execution_snapshot_check(p.id);
 IF (NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,NEW.evaluation_id,NEW.generation_start_id,
 NEW.snapshot_input_sha256,NEW.snapshot_output_sha256,NEW.snapshot_manifest_sha256,NEW.status) IS DISTINCT FROM
 (p.owner_id,p.run_id,p.attempt_id,p.generation,p.evaluation_id,p.start_id,o.input_sha256,o.output_sha256,o.manifest_sha256,'valid'::varchar)
 OR (b.owner_id,b.run_id,b.attempt_id,b.generation,b.start_id,b.controller_launch_id) IS DISTINCT FROM
 (NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,NEW.generation_start_id,NEW.controller_launch_id)
 OR NEW.feedback_object_kind<>'factor_generation_feedback'
 OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_objects r WHERE r.id=NEW.feedback_object_id AND r.owner_id=NEW.owner_id
   AND r.object_kind=NEW.feedback_object_kind AND r.sha256=NEW.feedback_sha256)
 THEN RAISE EXCEPTION 'Execution evaluation snapshot/actor/feedback differs'; END IF;
 RETURN NEW;
END $$;
"""

# Check every retained intent, including pre-claim intents. No native parser or
# detached publication row is needed to classify the exact historical inventory.
NORMALIZER_DOWNGRADE_SQL = r"""
DO $$
DECLARE rec record; hdf jsonb; listing jsonb; manifest jsonb; expected jsonb;
BEGIN
 FOR rec IN SELECT a.id,li.payload,c.projection
 FROM @SCHEMA@.research_process_admissions a
 JOIN @SCHEMA@.research_process_receipts li ON li.admission_id=a.id AND li.kind='launch_intent'
 LEFT JOIN @SCHEMA@.factor_cutoff_executions c ON c.id=a.logical_operation_id
 AND (c.owner_id,c.run_id,c.attempt_id,c.generation)=(a.owner_id,a.run_id,a.attempt_id,a.generation)
 WHERE a.schema_version='mineagent.process-admission.v2' AND a.role='factor_normalizer' LOOP
  IF rec.projection IS NULL OR rec.payload->>'schema_version' IS DISTINCT FROM 'mineagent.numeric-process-intent.v2'
  THEN RAISE EXCEPTION 'Unclassified normalizer intent blocks downgrade'; END IF;
  SELECT value INTO STRICT hdf FROM jsonb_array_elements(rec.payload->'inputs'->'files')
  WHERE value->>'destination'='/input/result.h5';
  IF jsonb_typeof(hdf) IS DISTINCT FROM 'object'
  OR NOT hdf ?& ARRAY['destination','sha256','size_bytes']
  OR (hdf-ARRAY['destination','sha256','size_bytes'])<>'{}'::jsonb
  OR jsonb_typeof(hdf->'sha256') IS DISTINCT FROM 'string'
  OR (hdf->>'sha256' ~ '^[a-f0-9]{64}$') IS NOT TRUE
  OR jsonb_typeof(hdf->'size_bytes') IS DISTINCT FROM 'number'
  OR (hdf->>'size_bytes' ~ '^[1-9][0-9]*$') IS NOT TRUE
  THEN RAISE EXCEPTION 'Unclassified native input blocks downgrade'; END IF;
  IF ((hdf->>'size_bytes')::bigint BETWEEN 1 AND 16777216) IS NOT TRUE
  THEN RAISE EXCEPTION 'Unbounded native input blocks downgrade'; END IF;
  SELECT value INTO STRICT listing FROM jsonb_array_elements(rec.projection->'input_files')
  WHERE value->>'name'='listings.json';
  manifest:=jsonb_build_object('schema_version','mineagent.factor-normalization.v1',
   'hdf_sha256',hdf->>'sha256','listings_sha256',listing->>'sha256',
   'output_event_ns',(extract(epoch FROM(rec.projection->>'output_event_time')::timestamptz)*1000000000)::bigint,
   'max_rows',rec.projection->'max_output_rows','max_bytes',rec.projection->'max_output_bytes');
  expected:=jsonb_build_array(
   jsonb_build_object('destination','/input/listings.json','sha256',listing->>'sha256','size_bytes',listing->'size_bytes'),
   jsonb_build_object('destination','/input/manifest.json',
    'sha256',encode(sha256(convert_to(@SCHEMA@.mineagent_native_metadata(manifest),'UTF8')),'hex'),
    'size_bytes',octet_length(@SCHEMA@.mineagent_native_metadata(manifest))),hdf);
  IF rec.payload->'inputs'->'files' IS DISTINCT FROM expected
  THEN RAISE EXCEPTION 'Report or unresolved normalizer intent blocks downgrade'; END IF;
 END LOOP;
END $$;
"""
