"""Literal026 fragments; RD migration owner installs them once, never on import."""

TABLES = r"""
CREATE TABLE rd_steers (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 current_version_id uuid NOT NULL, disabled boolean NOT NULL DEFAULT false,
 revision integer NOT NULL DEFAULT 1 CHECK(revision BETWEEN 1 AND 1000000),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 UNIQUE(owner_id,id)
);
CREATE TABLE rd_steer_versions (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, steer_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 64),
 kind varchar(20) NOT NULL CHECK(kind IN ('direction','hypothesis','constraints')),
 text text NOT NULL CHECK(length(text) BETWEEN 1 AND 2048 AND octet_length(text)<=8192 AND length(btrim(text))>0),
 text_sha256 varchar(64) NOT NULL CHECK(text_sha256 ~ '^[a-f0-9]{64}$'),
 content_canonical text NOT NULL CHECK(octet_length(content_canonical) BETWEEN 1 AND 32768),
 content_sha256 varchar(64) NOT NULL CHECK(content_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL,
 UNIQUE(owner_id,id), UNIQUE(owner_id,steer_id,id), UNIQUE(steer_id,ordinal),
 FOREIGN KEY(owner_id,steer_id) REFERENCES rd_steers(owner_id,id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(text_sha256=encode(sha256(convert_to(text,'UTF8')),'hex')),
 CHECK(content_sha256=encode(sha256(convert_to(content_canonical,'UTF8')),'hex')),
 -- Preserve exact Python canonical bytes and hash; compare parsed semantics.
 -- PostgreSQL to_json Unicode formatting is NOT used to reconstruct bytes.
 CHECK(content_canonical::jsonb=jsonb_build_object('schema_version','mineagent.rdagent-steer-version.v1','steer_id',steer_id::text,'version_id',id::text,'ordinal',ordinal,'kind',kind,'text',text,'text_sha256',text_sha256))
);
ALTER TABLE rd_steers ADD CONSTRAINT fk_rd_steer_head FOREIGN KEY(owner_id,id,current_version_id)
 REFERENCES rd_steer_versions(owner_id,steer_id,id) DEFERRABLE INITIALLY DEFERRED;
"""
UPGRADE = r"""
CREATE FUNCTION mineagent_steer_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Steer versions are immutable'; END $$;
CREATE TRIGGER rd_steer_version_immutable BEFORE UPDATE OR DELETE ON rd_steer_versions
 FOR EACH ROW EXECUTE FUNCTION mineagent_steer_immutable();
CREATE FUNCTION mineagent_steer_head_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Disable steer for future selection instead'; END IF;
 IF (NEW.id,NEW.owner_id,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.owner_id,OLD.created_at)
 OR NEW.revision<>OLD.revision+1 OR NEW.updated_at<OLD.updated_at
 THEN RAISE EXCEPTION 'Steer immutable identity or revision differs'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rd_steer_head_update BEFORE UPDATE OR DELETE ON rd_steers
 FOR EACH ROW EXECUTE FUNCTION mineagent_steer_head_update();
CREATE FUNCTION mineagent_steer_graph_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; head rd_steers%ROWTYPE; n integer; lo integer; hi integer; vid uuid;
BEGIN
 IF TG_TABLE_NAME='rd_steers' THEN
  sid:=NEW.id;
 ELSE
  sid:=NEW.steer_id;
 END IF;
 SELECT * INTO head FROM rd_steers WHERE id=sid;
 SELECT count(*),min(ordinal),max(ordinal) INTO n,lo,hi FROM rd_steer_versions WHERE steer_id=sid AND owner_id=head.owner_id;
 SELECT id INTO vid FROM rd_steer_versions WHERE steer_id=sid AND ordinal=hi AND owner_id=head.owner_id;
 IF head.id IS NULL OR n IS NULL OR n<1 OR lo<>1 OR n<>hi OR head.current_version_id IS DISTINCT FROM vid OR head.revision<hi
 THEN RAISE EXCEPTION 'Steer head and immutable version inventory differ'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER rd_steer_graph_head AFTER INSERT OR UPDATE ON rd_steers
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_steer_graph_check();
CREATE CONSTRAINT TRIGGER rd_steer_graph_version AFTER INSERT ON rd_steer_versions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_steer_graph_check();
"""
DOWNGRADE = r"""
DROP TRIGGER rd_steer_graph_version ON rd_steer_versions;
DROP TRIGGER rd_steer_graph_head ON rd_steers;
DROP FUNCTION mineagent_steer_graph_check();
DROP TRIGGER rd_steer_head_update ON rd_steers;
DROP FUNCTION mineagent_steer_head_update();
DROP TRIGGER rd_steer_version_immutable ON rd_steer_versions;
DROP FUNCTION mineagent_steer_immutable();
ALTER TABLE rd_steers DROP CONSTRAINT fk_rd_steer_head;
DROP TABLE rd_steer_versions;
DROP TABLE rd_steers;
"""
