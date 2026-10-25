"""Independent append-only factor library, without changing legacy identities."""

from alembic import op

revision = "012_factor_library"
down_revision = "011_note_pin_trigger"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""

CREATE TABLE factors (
	name VARCHAR(240) NOT NULL,
	category VARCHAR(120),
	bookmarked BOOLEAN NOT NULL,
	presentation_revision INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	CHECK (presentation_revision > 0),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_objects (
	storage_key VARCHAR(300) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes BIGINT NOT NULL,
	media_type VARCHAR(100) NOT NULL,
	object_kind VARCHAR(40) NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	UNIQUE (owner_id, storage_key),
	UNIQUE (owner_id, id, sha256),
	CHECK (size_bytes >= 0),
	CHECK (media_type = CASE object_kind WHEN 'code' THEN 'text/plain' WHEN 'values_partition' THEN 'application/vnd.apache.parquet' WHEN 'package' THEN 'application/zip' ELSE 'application/json' END),
	CHECK (storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || CASE object_kind WHEN 'code' THEN '.py' WHEN 'values_partition' THEN '.parquet' WHEN 'package' THEN '.zip' ELSE '.json' END),
	CHECK (sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (object_kind IN ('code','snapshot_manifest','values_partition','calculation_samples','analysis_manifest','analysis_series','native_evidence','package')),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_versions (
	factor_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	parent_version_id UUID,
	definition JSONB NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	code TEXT NOT NULL,
	code_sha256 VARCHAR(64) NOT NULL,
	code_object_id UUID NOT NULL,
	source_kind VARCHAR(40) NOT NULL,
	source_event_key VARCHAR(128),
	source_provenance JSONB NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, factor_id) REFERENCES factors (owner_id, id),
	FOREIGN KEY(owner_id, parent_version_id) REFERENCES factor_versions (owner_id, id),
	FOREIGN KEY(owner_id, code_object_id, code_sha256) REFERENCES factor_objects (owner_id, id, sha256),
	UNIQUE (factor_id, ordinal),
	UNIQUE (owner_id, source_kind, source_event_key),
	UNIQUE (owner_id, id, definition_sha256),
	CHECK (ordinal > 0),
	CHECK (octet_length(code) <= 131072),
	CHECK (definition_sha256 ~ '^[0-9a-f]{64}$' AND code_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (definition->>'code_sha256' = code_sha256),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE feature_snapshots (
	factor_version_id UUID NOT NULL,
	dataset_version_id UUID NOT NULL,
	dataset_content_sha256 VARCHAR(64) NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	input_digest VARCHAR(64) NOT NULL,
	output_digest VARCHAR(64) NOT NULL,
	manifest_object_id UUID NOT NULL,
	manifest_sha256 VARCHAR(64) NOT NULL,
	manifest JSONB NOT NULL,
	environment_digest VARCHAR(64) NOT NULL,
	row_count BIGINT NOT NULL,
	valid_count BIGINT NOT NULL,
	missing_count BIGINT NOT NULL,
	identity_mode VARCHAR(30) NOT NULL,
	local_verification JSONB NOT NULL,
	source_claims JSONB NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, factor_version_id, definition_sha256) REFERENCES factor_versions (owner_id, id, definition_sha256),
	FOREIGN KEY(owner_id, manifest_object_id, manifest_sha256) REFERENCES factor_objects (owner_id, id, sha256),
	UNIQUE (owner_id, id, factor_version_id),
	UNIQUE (owner_id, input_digest, output_digest),
	CHECK (row_count = valid_count + missing_count AND valid_count >= 0 AND missing_count >= 0),
	CHECK (identity_mode = 'local'),
	FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE feature_snapshot_objects (
	snapshot_id UUID NOT NULL,
	object_id UUID NOT NULL,
	role VARCHAR(40) NOT NULL,
	ordinal INTEGER NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, snapshot_id) REFERENCES feature_snapshots (owner_id, id),
	FOREIGN KEY(owner_id, object_id) REFERENCES factor_objects (owner_id, id),
	UNIQUE (snapshot_id, role, ordinal),
	UNIQUE (snapshot_id, object_id),
	CHECK (ordinal >= 0),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_evaluations (
	factor_version_id UUID NOT NULL,
	feature_snapshot_id UUID,
	kind VARCHAR(40) NOT NULL,
	protocol JSONB NOT NULL,
	protocol_sha256 VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	result_payload JSONB NOT NULL,
	result_sha256 VARCHAR(64) NOT NULL,
	environment_digest VARCHAR(64) NOT NULL,
	error_code VARCHAR(80),
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, factor_version_id) REFERENCES factor_versions (owner_id, id),
	FOREIGN KEY(owner_id, feature_snapshot_id, factor_version_id) REFERENCES feature_snapshots (owner_id, id, factor_version_id),
	CHECK (kind IN ('materialization','direct_factor_analysis','native_prediction_analysis','native_portfolio_analysis')),
	CHECK (status IN ('succeeded','failed','cancelled')),
	CHECK (status <> 'succeeded' OR feature_snapshot_id IS NOT NULL),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_bundles (
	name VARCHAR(240) NOT NULL,
	definition JSONB NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_bundle_members (
	bundle_id UUID NOT NULL,
	factor_version_id UUID NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	ordinal INTEGER NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, bundle_id) REFERENCES factor_bundles (owner_id, id),
	FOREIGN KEY(owner_id, factor_version_id, definition_sha256) REFERENCES factor_versions (owner_id, id, definition_sha256),
	UNIQUE (bundle_id, factor_version_id),
	UNIQUE (bundle_id, ordinal),
	CHECK (ordinal >= 0),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE strategy_factor_dependencies (
	strategy_version_id UUID NOT NULL,
	factor_bundle_id UUID NOT NULL,
	dependency_sha256 VARCHAR(64) NOT NULL,
	definition JSONB NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, factor_bundle_id) REFERENCES factor_bundles (owner_id, id),
	UNIQUE (owner_id, strategy_version_id),
	FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions (id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_submissions (
	operation VARCHAR(80) NOT NULL,
	idempotency_key VARCHAR(128) NOT NULL,
	request_sha256 VARCHAR(64) NOT NULL,
	resource_id UUID NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	UNIQUE (owner_id, operation, idempotency_key),
	CHECK (idempotency_key ~ '^[!-~]{1,128}$'),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_comparisons (
	left_version_id UUID NOT NULL,
	right_version_id UUID NOT NULL,
	definition JSONB NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, left_version_id) REFERENCES factor_versions (owner_id, id),
	FOREIGN KEY(owner_id, right_version_id) REFERENCES factor_versions (owner_id, id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE factor_packages (
	factor_version_id UUID NOT NULL,
	archive_object_id UUID NOT NULL,
	archive_sha256 VARCHAR(64) NOT NULL,
	definition JSONB NOT NULL,
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, factor_version_id) REFERENCES factor_versions (owner_id, id),
	FOREIGN KEY(owner_id, archive_object_id, archive_sha256) REFERENCES factor_objects (owner_id, id, sha256),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;
CREATE INDEX ix_factors_owner_id ON factors (owner_id);
CREATE INDEX ix_factor_objects_owner_id ON factor_objects (owner_id);
CREATE INDEX ix_factor_versions_owner_id ON factor_versions (owner_id);
CREATE INDEX ix_feature_snapshots_owner_id ON feature_snapshots (owner_id);
CREATE INDEX ix_feature_snapshot_objects_owner_id ON feature_snapshot_objects (owner_id);
CREATE INDEX ix_factor_evaluations_owner_id ON factor_evaluations (owner_id);
CREATE INDEX ix_factor_bundles_owner_id ON factor_bundles (owner_id);
CREATE INDEX ix_factor_bundle_members_owner_id ON factor_bundle_members (owner_id);
CREATE INDEX ix_strategy_factor_dependencies_owner_id ON strategy_factor_dependencies (owner_id);
CREATE INDEX ix_factor_submissions_owner_id ON factor_submissions (owner_id);
CREATE INDEX ix_factor_comparisons_owner_id ON factor_comparisons (owner_id);
CREATE INDEX ix_factor_packages_owner_id ON factor_packages (owner_id);
"""

INVARIANTS_SQL = r"""

CREATE FUNCTION reject_factor_evidence_change() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Factor evidence is append-only'; END; $$ LANGUAGE plpgsql;
CREATE FUNCTION validate_factor_version() RETURNS trigger AS $$
DECLARE latest INTEGER;
BEGIN
  PERFORM 1 FROM factors WHERE id=NEW.factor_id AND owner_id=NEW.owner_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Factor owner mismatch'; END IF;
  SELECT coalesce(max(ordinal),0) INTO latest FROM factor_versions WHERE factor_id=NEW.factor_id;
  IF NEW.ordinal <> latest + 1 THEN RAISE EXCEPTION 'Factor ordinals must be consecutive'; END IF;
  IF NEW.parent_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM factor_versions WHERE id=NEW.parent_version_id AND owner_id=NEW.owner_id
      AND factor_id=NEW.factor_id AND ordinal<NEW.ordinal
  ) THEN RAISE EXCEPTION 'Factor parent tuple mismatch'; END IF;
  IF NOT EXISTS (SELECT 1 FROM factor_objects WHERE id=NEW.code_object_id AND owner_id=NEW.owner_id
    AND sha256=NEW.code_sha256 AND object_kind='code' AND size_bytes=octet_length(NEW.code))
  THEN RAISE EXCEPTION 'Factor code object mismatch'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_factor_version BEFORE INSERT ON factor_versions FOR EACH ROW EXECUTE FUNCTION validate_factor_version();
CREATE FUNCTION validate_factor_snapshot() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM dataset_versions WHERE id=NEW.dataset_version_id
    AND owner_id=NEW.owner_id AND content_sha256=NEW.dataset_content_sha256)
  THEN RAISE EXCEPTION 'Factor dataset tuple mismatch'; END IF;
  IF NOT EXISTS (SELECT 1 FROM factor_objects WHERE id=NEW.manifest_object_id AND owner_id=NEW.owner_id
    AND sha256=NEW.manifest_sha256 AND object_kind='snapshot_manifest')
  THEN RAISE EXCEPTION 'Factor manifest object mismatch'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_factor_snapshot BEFORE INSERT ON feature_snapshots FOR EACH ROW EXECUTE FUNCTION validate_factor_snapshot();
CREATE FUNCTION validate_factor_snapshot_object() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM factor_objects WHERE id=NEW.object_id AND owner_id=NEW.owner_id
    AND object_kind=NEW.role) THEN RAISE EXCEPTION 'Factor object role mismatch'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_factor_snapshot_object BEFORE INSERT ON feature_snapshot_objects FOR EACH ROW EXECUTE FUNCTION validate_factor_snapshot_object();
CREATE FUNCTION validate_strategy_factor_dependency() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM strategy_versions WHERE id=NEW.strategy_version_id AND owner_id=NEW.owner_id)
  THEN RAISE EXCEPTION 'Factor strategy owner mismatch'; END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_strategy_factor_dependency BEFORE INSERT ON strategy_factor_dependencies FOR EACH ROW EXECUTE FUNCTION validate_strategy_factor_dependency();

CREATE FUNCTION validate_factor_snapshot_closure() RETURNS trigger AS $$
DECLARE snap feature_snapshots%ROWTYPE; declared_count INTEGER; actual_count INTEGER;
BEGIN
  IF TG_TABLE_NAME='feature_snapshots' THEN SELECT * INTO snap FROM feature_snapshots WHERE id=NEW.id;
  ELSE SELECT * INTO snap FROM feature_snapshots WHERE id=NEW.snapshot_id; END IF;
  IF snap.manifest->>'snapshot_id' IS DISTINCT FROM snap.id::text
     OR snap.manifest#>>'{input,version,factor_version_id}' IS DISTINCT FROM snap.factor_version_id::text
     OR snap.manifest#>>'{input,version,definition_sha256}' IS DISTINCT FROM snap.definition_sha256
     OR snap.manifest#>>'{input,dataset,dataset_version_id}' IS DISTINCT FROM snap.dataset_version_id::text
     OR snap.manifest#>>'{input,dataset,content_sha256}' IS DISTINCT FROM snap.dataset_content_sha256
     OR snap.manifest#>>'{input,environment_sha256}' IS DISTINCT FROM snap.environment_digest
     OR snap.manifest->>'input_digest' IS DISTINCT FROM snap.input_digest
     OR snap.manifest->>'output_digest' IS DISTINCT FROM snap.output_digest
     OR (snap.manifest->>'row_count')::bigint IS DISTINCT FROM snap.row_count
     OR (snap.manifest->>'valid_count')::bigint IS DISTINCT FROM snap.valid_count
     OR (snap.manifest->>'missing_count')::bigint IS DISTINCT FROM snap.missing_count
  THEN RAISE EXCEPTION 'Factor stored manifest tuple mismatch'; END IF;
  SELECT jsonb_array_length(snap.manifest->'objects') INTO declared_count;
  SELECT count(*) INTO actual_count FROM feature_snapshot_objects WHERE snapshot_id=snap.id;
  IF declared_count IS NULL OR declared_count<>actual_count OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(snap.manifest->'objects') m WHERE NOT EXISTS (
      SELECT 1 FROM feature_snapshot_objects a JOIN factor_objects o ON o.id=a.object_id
      WHERE a.snapshot_id=snap.id AND a.owner_id=snap.owner_id AND o.owner_id=snap.owner_id
        AND a.role=m->>'role' AND a.ordinal=(m->>'ordinal')::integer
        AND o.sha256=m->>'sha256' AND o.size_bytes=(m->>'size_bytes')::bigint
    )
  ) THEN RAISE EXCEPTION 'Factor manifest object inventory mismatch'; END IF;
  RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER validate_factor_snapshot_closure AFTER INSERT ON feature_snapshots
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_factor_snapshot_closure();
CREATE CONSTRAINT TRIGGER validate_factor_inventory_closure AFTER INSERT ON feature_snapshot_objects
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_factor_snapshot_closure();
CREATE FUNCTION validate_factor_bundle_closure() RETURNS trigger AS $$
DECLARE target UUID; bundle factor_bundles%ROWTYPE; n INTEGER;
BEGIN
  IF TG_TABLE_NAME='factor_bundles' THEN target=NEW.id; ELSE target=NEW.bundle_id; END IF;
  SELECT * INTO bundle FROM factor_bundles WHERE id=target;
  SELECT count(*) INTO n FROM factor_bundle_members WHERE bundle_id=target;
  IF n IS DISTINCT FROM jsonb_array_length(bundle.definition->'members') OR n=0 OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(bundle.definition->'members') WITH ORDINALITY m(value,ord)
    WHERE NOT EXISTS (SELECT 1 FROM factor_bundle_members b WHERE b.bundle_id=target
      AND b.ordinal=m.ord-1 AND b.factor_version_id::text=m.value->>'factor_version_id'
      AND b.definition_sha256=m.value->>'definition_sha256')
  ) THEN RAISE EXCEPTION 'Factor bundle inventory mismatch'; END IF;
  RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER validate_factor_bundle_closure AFTER INSERT ON factor_bundles
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_factor_bundle_closure();
CREATE CONSTRAINT TRIGGER validate_factor_member_closure AFTER INSERT ON factor_bundle_members
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_factor_bundle_closure();

CREATE FUNCTION validate_factor_evaluation() RETURNS trigger AS $$
DECLARE snap feature_snapshots%ROWTYPE;
BEGIN
  IF NEW.feature_snapshot_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO snap FROM feature_snapshots WHERE id=NEW.feature_snapshot_id
    AND owner_id=NEW.owner_id AND factor_version_id=NEW.factor_version_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Factor evaluation snapshot tuple mismatch'; END IF;
  IF NEW.status='succeeded' AND NEW.kind='materialization' THEN
    IF NEW.environment_digest IS DISTINCT FROM snap.environment_digest
      OR NEW.protocol->>'input_digest' IS DISTINCT FROM snap.input_digest
      OR coalesce(NEW.result_payload->>'output_digest',NEW.result_payload->>'rows_sha256') IS DISTINCT FROM snap.output_digest
      OR (NEW.result_payload->>'row_count')::bigint IS DISTINCT FROM snap.row_count
      OR (NEW.result_payload->>'valid_count')::bigint IS DISTINCT FROM snap.valid_count
      OR (NEW.result_payload->>'missing_count')::bigint IS DISTINCT FROM snap.missing_count
    THEN RAISE EXCEPTION 'Factor materialization result tuple mismatch'; END IF;
  ELSIF NEW.status='succeeded' THEN
    IF NEW.protocol->>'snapshot_id' IS DISTINCT FROM snap.id::text
      OR NEW.protocol->>'snapshot_output_sha256' IS DISTINCT FROM snap.output_digest
      OR NEW.protocol->>'kind' IS DISTINCT FROM NEW.kind
    THEN RAISE EXCEPTION 'Factor analysis result tuple mismatch'; END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_factor_evaluation BEFORE INSERT ON factor_evaluations
  FOR EACH ROW EXECUTE FUNCTION validate_factor_evaluation();

"""

TABLES = (
    "factors",
    "factor_objects",
    "factor_versions",
    "feature_snapshots",
    "feature_snapshot_objects",
    "factor_evaluations",
    "factor_bundles",
    "factor_bundle_members",
    "strategy_factor_dependencies",
    "factor_submissions",
    "factor_comparisons",
    "factor_packages",
)


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(INVARIANTS_SQL)
    for table in TABLES[1:]:
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_factor_evidence_change()"
        )


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    for function in (
        "validate_factor_evaluation",
        "validate_factor_bundle_closure",
        "validate_factor_snapshot_closure",
        "validate_strategy_factor_dependency",
        "validate_factor_snapshot_object",
        "validate_factor_snapshot",
        "validate_factor_version",
        "reject_factor_evidence_change",
    ):
        op.execute(f"DROP FUNCTION {function}()")
