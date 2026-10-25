"""Independent supervised factor import admission and immutable origin envelope."""

from alembic import op

revision = "013_factor_import"
down_revision = "012_factor_library"
branch_labels = None
depends_on = None

SCHEMA_SQL = "\nCREATE TABLE factor_import_envelopes (\n\tarchive_object_id UUID NOT NULL, \n\tarchive_sha256 VARCHAR(64) NOT NULL, \n\torigin_namespace VARCHAR(180) NOT NULL, \n\torigin_payload JSONB NOT NULL, \n\torigin_payload_sha256 VARCHAR(64) NOT NULL, \n\tlocal_bindings JSONB NOT NULL, \n\tlocal_bindings_sha256 VARCHAR(64) NOT NULL, \n\texposure_history JSONB NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, id), \n\tUNIQUE (owner_id, archive_sha256), \n\tUNIQUE (owner_id, id, archive_sha256), \n\tFOREIGN KEY(owner_id, archive_object_id, archive_sha256) REFERENCES factor_objects (owner_id, id, sha256), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n;\n\nCREATE TABLE factor_import_admissions (\n\tarchive_object_id UUID NOT NULL, \n\tarchive_sha256 VARCHAR(64) NOT NULL, \n\tidempotency_key VARCHAR(128) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tparser_image_id VARCHAR(71) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tlease_token UUID, \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tresult_envelope_id UUID, \n\terror_code VARCHAR(80), \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, id), \n\tUNIQUE (owner_id, idempotency_key), \n\tFOREIGN KEY(owner_id, result_envelope_id, archive_sha256) REFERENCES factor_import_envelopes (owner_id, id, archive_sha256), \n\tFOREIGN KEY(owner_id, archive_object_id, archive_sha256) REFERENCES factor_objects (owner_id, id, sha256), \n\tCHECK (status IN ('queued','processing','succeeded','failed','blocked')), \n\tCHECK (generation >= 0), \n\tCHECK (parser_image_id ~ '^sha256:[0-9a-f]{64}$'), \n\tCHECK ((status = 'succeeded') = (result_envelope_id IS NOT NULL)), \n\tCHECK (status <> 'processing' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)), \n\tCHECK (status NOT IN ('succeeded','failed') OR (lease_token IS NULL AND lease_expires_at IS NULL)), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n;\nCREATE INDEX ix_factor_import_envelopes_owner_id ON factor_import_envelopes (owner_id);\nCREATE INDEX ix_factor_import_admissions_owner_id ON factor_import_admissions (owner_id);"

INVARIANTS_SQL = r"""
CREATE TRIGGER immutable_factor_import_envelopes BEFORE UPDATE OR DELETE ON factor_import_envelopes
FOR EACH ROW EXECUTE FUNCTION reject_factor_evidence_change();
CREATE FUNCTION validate_factor_import_transition() RETURNS trigger AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Import admission is retained'; END IF;
 IF OLD.status IN ('succeeded','failed') THEN RAISE EXCEPTION 'Terminal import admission is immutable'; END IF;
 IF (NEW.id,NEW.owner_id,NEW.archive_object_id,NEW.archive_sha256,NEW.idempotency_key,NEW.request_sha256,NEW.parser_image_id,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.owner_id,OLD.archive_object_id,OLD.archive_sha256,OLD.idempotency_key,OLD.request_sha256,OLD.parser_image_id,OLD.created_at)
 THEN RAISE EXCEPTION 'Import identity is immutable'; END IF;
 IF NEW.generation < OLD.generation OR NEW.generation > OLD.generation+1
 THEN RAISE EXCEPTION 'Import generation is fenced'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER validate_factor_import_transition BEFORE UPDATE OR DELETE ON factor_import_admissions
FOR EACH ROW EXECUTE FUNCTION validate_factor_import_transition();
"""


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(INVARIANTS_SQL)


def downgrade():
    # Empty feature schemas may roll back without blocking legacy migrations.
    # Pending work, immutable evidence and external import dependencies never do.
    # NOWAIT table locks also exclude noncooperative/raw SQL insert races.
    op.execute("""
    DO $$ BEGIN
      IF current_setting('transaction_isolation') <> 'read committed'
      THEN RAISE EXCEPTION 'Factor downgrade requires READ COMMITTED isolation for authoritative emptiness checks'; END IF;
      IF NOT pg_try_advisory_xact_lock(1413960260, 1129270340)
      THEN RAISE EXCEPTION 'Factor import migration requires quiescent maintenance admission'; END IF;
    END $$;
    LOCK TABLE factor_import_admissions, factor_import_envelopes,
      factor_versions, feature_snapshots, data_usage_exposures, dataset_versions
      IN ACCESS EXCLUSIVE MODE NOWAIT;
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM factor_import_admissions)
        OR EXISTS (SELECT 1 FROM factor_import_envelopes)
        OR EXISTS (SELECT 1 FROM factor_versions
          WHERE source_kind = 'imported' OR source_provenance ? 'import_envelope_id')
        OR EXISTS (SELECT 1 FROM feature_snapshots
          WHERE source_claims ? 'import_envelope_id'
            OR local_verification->>'admission_profile' = 'isolated-import.v1')
        OR EXISTS (SELECT 1 FROM data_usage_exposures
          WHERE source_key LIKE 'factor:import:%'
            OR purpose = 'imported_factor_research_exposure')
        OR EXISTS (SELECT 1 FROM dataset_versions WHERE source = 'imported_factor_synthetic')
      THEN RAISE EXCEPTION 'Factor import evidence and retained dependencies must be preserved before downgrade';
      END IF;
    END $$;
    DROP TABLE factor_import_admissions;
    DROP TABLE factor_import_envelopes;
    DROP FUNCTION validate_factor_import_transition();
    """)
