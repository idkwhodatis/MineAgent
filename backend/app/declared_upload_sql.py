"""030 relational binding, not a claim to validate raw CSV or corporate actions."""

TABLE_SQL = r"""
CREATE TABLE @SCHEMA@.declared_upload_baskets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, idempotency_key varchar(128) NOT NULL,
 request_sha256 varchar(64) NOT NULL, dataset_id uuid NOT NULL, manifest_sha256 varchar(64) NOT NULL,
 profile varchar(80) NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(owner_id,idempotency_key), UNIQUE(dataset_id), UNIQUE(owner_id,id),
 FOREIGN KEY(owner_id,dataset_id,manifest_sha256) REFERENCES @SCHEMA@.dataset_versions(owner_id,id,content_sha256) ON DELETE RESTRICT,
 CHECK(idempotency_key ~ '^[A-Za-z0-9_.:-]{1,128}$'),
 CHECK(request_sha256 ~ '^[a-f0-9]{64}$' AND manifest_sha256 ~ '^[a-f0-9]{64}$'),
 CHECK(profile='mineagent.declared-upload-basket.v1')
);
CREATE TABLE @SCHEMA@.declared_upload_basket_members (
 basket_id uuid NOT NULL, ordinal integer NOT NULL, owner_id uuid NOT NULL,
 source_dataset_id uuid NOT NULL, source_manifest_sha256 varchar(64) NOT NULL, created_at timestamptz NOT NULL,
 PRIMARY KEY(basket_id,ordinal), UNIQUE(basket_id,source_dataset_id),
 FOREIGN KEY(owner_id,basket_id) REFERENCES @SCHEMA@.declared_upload_baskets(owner_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(owner_id,source_dataset_id,source_manifest_sha256) REFERENCES @SCHEMA@.dataset_versions(owner_id,id,content_sha256) ON DELETE RESTRICT,
 CHECK(ordinal IN (0,1)), CHECK(source_manifest_sha256 ~ '^[a-f0-9]{64}$')
);
"""

BINDINGS = r"""
CREATE FUNCTION @SCHEMA@.mineagent_upload_basket_check(bid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE h record; d record; m record; s record; j jsonb; item jsonb; previous uuid;
BEGIN
 SELECT * INTO STRICT h FROM @SCHEMA@.declared_upload_baskets WHERE id=bid;
 SELECT * INTO STRICT d FROM @SCHEMA@.dataset_versions WHERE id=h.dataset_id FOR SHARE;
 j:=d.manifest;
 IF (d.owner_id,d.content_sha256,d.source,d.market,d.is_synthetic) IS DISTINCT FROM
 (h.owner_id,h.manifest_sha256,'declared_upload_basket'::text,'US'::text,false)
 OR d.storage_key IS DISTINCT FROM 'datasets/'||h.owner_id::text||'/'||d.id::text
 OR j->'schema_version' IS DISTINCT FROM '3'::jsonb
 OR j->>'profile' IS DISTINCT FROM h.profile
 OR j->>'owner_id' IS DISTINCT FROM h.owner_id::text
 OR j->>'dataset_version_id' IS DISTINCT FROM d.id::text
 OR j->>'start' IS DISTINCT FROM d.start_date::text OR j->>'end' IS DISTINCT FROM d.end_date::text
 OR j->>'market' IS DISTINCT FROM 'US' OR j->'is_synthetic' IS DISTINCT FROM 'false'::jsonb
 OR j->>'data_evidence' IS DISTINCT FROM 'user_declaration_unverified'
 OR j->'point_in_time_proven' IS DISTINCT FROM 'false'::jsonb
 OR jsonb_typeof(j->'sources') IS DISTINCT FROM 'array'
 OR jsonb_typeof(j->'listings') IS DISTINCT FROM 'array'
 OR jsonb_typeof(j->'artifact_files') IS DISTINCT FROM 'object'
 THEN RAISE EXCEPTION 'Declared upload target identity differs'; END IF;
 IF jsonb_array_length(j->'sources')<>2 OR jsonb_array_length(j->'listings')<>2
 OR (SELECT count(*) FROM @SCHEMA@.declared_upload_basket_members WHERE basket_id=bid)<>2
 THEN RAISE EXCEPTION 'Declared upload requires exactly two sources'; END IF;
 FOR m IN SELECT * FROM @SCHEMA@.declared_upload_basket_members WHERE basket_id=bid ORDER BY ordinal LOOP
  SELECT * INTO STRICT s FROM @SCHEMA@.dataset_versions WHERE id=m.source_dataset_id FOR SHARE;
  item:=j->'sources'->m.ordinal;
  IF (m.owner_id,s.owner_id,s.content_sha256,s.source,s.market,s.is_synthetic,s.start_date,s.end_date) IS DISTINCT FROM
  (h.owner_id,h.owner_id,m.source_manifest_sha256,'upload'::text,'US'::text,false,d.start_date,d.end_date)
  OR s.id=d.id OR (previous IS NOT NULL AND s.id<=previous)
  OR s.manifest->'schema_version' IS DISTINCT FROM '2'::jsonb
  OR s.manifest->'is_synthetic' IS DISTINCT FROM 'false'::jsonb
  OR s.manifest#>'{evaluation_compatibility,compatible}' IS DISTINCT FROM 'true'::jsonb
  OR s.manifest#>>'{upload_source,artifact}' IS DISTINCT FROM 'source.csv'
  OR jsonb_typeof(s.manifest->'listings') IS DISTINCT FROM 'array'
  OR item IS DISTINCT FROM jsonb_build_object('ordinal',m.ordinal,'dataset_version_id',s.id::text,
    'manifest_sha256',s.content_sha256,'listing_id',j->'listings'->m.ordinal->>'listing_id',
    'artifact_prefix','sources/'||m.ordinal::text||'/')
  THEN RAISE EXCEPTION 'Declared upload source identity differs'; END IF;
  IF jsonb_array_length(s.manifest->'listings')<>1
  OR j->'listings'->m.ordinal IS DISTINCT FROM s.manifest->'listings'->0
  OR coalesce((j->'listings'->m.ordinal->>'listing_id') ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$',false) IS NOT TRUE
  OR j->'listings'->m.ordinal->>'mic' IS DISTINCT FROM 'XNYS'
  OR j->'listings'->m.ordinal->>'currency' IS DISTINCT FROM 'USD'
  THEN RAISE EXCEPTION 'Declared upload listing differs'; END IF;
  previous:=s.id;
 END LOOP;
 IF h.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(@SCHEMA@.mineagent_rd_ascii_metadata(
  jsonb_build_object('source_dataset_ids',(SELECT jsonb_agg(source_dataset_id::text ORDER BY ordinal) FROM @SCHEMA@.declared_upload_basket_members WHERE basket_id=bid),
  'acknowledge_unverified',true,'idempotency_key',h.idempotency_key)),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Declared upload request commitment differs'; END IF;
 IF j->'listings'->0->>'listing_id' IS NOT DISTINCT FROM j->'listings'->1->>'listing_id'
 THEN RAISE EXCEPTION 'Declared upload listings must differ'; END IF;
END $$;

CREATE FUNCTION @SCHEMA@.mineagent_upload_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'Declared upload relation is immutable'; END $$;
CREATE TRIGGER upload030_immutable BEFORE UPDATE OR DELETE ON @SCHEMA@.declared_upload_baskets
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_immutable();
CREATE TRIGGER upload030_immutable BEFORE UPDATE OR DELETE ON @SCHEMA@.declared_upload_basket_members
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_immutable();

CREATE FUNCTION @SCHEMA@.mineagent_upload_deferred() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='declared_upload_baskets' THEN
  PERFORM @SCHEMA@.mineagent_upload_basket_check(NEW.id);
 ELSE PERFORM @SCHEMA@.mineagent_upload_basket_check(NEW.basket_id); END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER upload030_complete AFTER INSERT ON @SCHEMA@.declared_upload_baskets
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_deferred();
CREATE CONSTRAINT TRIGGER upload030_complete AFTER INSERT ON @SCHEMA@.declared_upload_basket_members
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_deferred();

CREATE FUNCTION @SCHEMA@.mineagent_upload_dataset_deferred() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE bid uuid;
BEGIN
 IF NEW.source='declared_upload_basket' OR NEW.manifest->>'profile'='mineagent.declared-upload-basket.v1' THEN
  SELECT id INTO STRICT bid FROM @SCHEMA@.declared_upload_baskets WHERE dataset_id=NEW.id AND owner_id=NEW.owner_id;
  PERFORM @SCHEMA@.mineagent_upload_basket_check(bid);
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER upload030_dataset_complete AFTER INSERT OR UPDATE ON @SCHEMA@.dataset_versions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_dataset_deferred();

CREATE FUNCTION @SCHEMA@.mineagent_upload_dataset_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.declared_upload_baskets WHERE dataset_id=OLD.id)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.declared_upload_basket_members WHERE source_dataset_id=OLD.id)
 THEN RAISE EXCEPTION 'Declared upload referenced dataset is immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER upload030_dataset_immutable BEFORE UPDATE OR DELETE ON @SCHEMA@.dataset_versions
 FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_upload_dataset_immutable();

CREATE FUNCTION @SCHEMA@.mineagent_upload_oe_qualification(owner uuid,did uuid,qualification text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE d record; h record;
BEGIN
 SELECT * INTO STRICT d FROM @SCHEMA@.dataset_versions WHERE id=did AND owner_id=owner;
 IF qualification='user_declaration_unverified' THEN
  SELECT * INTO STRICT h FROM @SCHEMA@.declared_upload_baskets WHERE dataset_id=did AND owner_id=owner;
  PERFORM @SCHEMA@.mineagent_upload_basket_check(h.id);
 ELSIF qualification='synthetic_by_construction' THEN
  IF d.manifest->>'profile'='mineagent.declared-upload-basket.v1' OR d.source='declared_upload_basket'
  THEN RAISE EXCEPTION 'Declared upload cannot acquire synthetic qualification'; END IF;
 ELSE RAISE EXCEPTION 'Unknown structured market qualification'; END IF;
END $$;
"""
