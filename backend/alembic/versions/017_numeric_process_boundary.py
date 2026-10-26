"""Numeric no-IPC persistence only; no launch grant or default entrypoint."""

from alembic import op

revision = "017_numeric_process_boundary"
down_revision = "016_process_versioning"
branch_labels = None
depends_on = None

UPGRADE_SQL = "ALTER TABLE research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,\n ADD CONSTRAINT ck_process_v2_unregistered CHECK ((schema_version = 'mineagent.process-admission.v1' OR\n (schema_version = 'mineagent.process-admission.v2' AND ipc_policy = 'none'\n  AND ticket_sha256 IS NULL AND (\n   (role = 'factor_cutoff_candidate' AND profile = 'mineagent.factor-prefix-candidate.v2') OR\n   (role = 'factor_normalizer' AND profile = 'mineagent.factor-numeric-hdf.v1')))) IS TRUE);\nALTER TABLE research_process_receipts DROP CONSTRAINT ck_research_process_receipt_kind,\n ADD CONSTRAINT ck_research_process_receipt_kind CHECK (kind IN ('launch_intent','launch_identity','stop','extraction')),\n ADD CONSTRAINT uq_process_receipt_scoped_kind UNIQUE(owner_id,run_id,attempt_id,generation,admission_id,id,kind);"


def maintenance():
    op.execute("""DO $$ BEGIN
      IF current_setting('transaction_isolation') <> 'read committed'
      THEN RAISE EXCEPTION 'Numeric persistence requires READ COMMITTED'; END IF;
      IF NOT pg_try_advisory_xact_lock(1413960260, 1129270340)
      THEN RAISE EXCEPTION 'Numeric persistence requires quiescent maintenance admission'; END IF;
    END $$;""")
    op.execute(
        "LOCK TABLE research_process_admissions, research_process_receipts IN ACCESS EXCLUSIVE MODE NOWAIT"
    )


def upgrade():
    maintenance()
    op.execute(UPGRADE_SQL)


def downgrade():
    maintenance()
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM research_process_admissions WHERE schema_version <> 'mineagent.process-admission.v1')
       OR EXISTS(SELECT 1 FROM research_process_receipts WHERE kind = 'extraction')
      THEN RAISE EXCEPTION 'Numeric process evidence prevents downgrade'; END IF;
    END $$;""")
    op.execute("""ALTER TABLE research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
      ADD CONSTRAINT ck_process_v2_unregistered CHECK ((schema_version = 'mineagent.process-admission.v1') IS TRUE);
    ALTER TABLE research_process_receipts DROP CONSTRAINT uq_process_receipt_scoped_kind,
      DROP CONSTRAINT ck_research_process_receipt_kind,
      ADD CONSTRAINT ck_research_process_receipt_kind CHECK (kind IN ('launch_intent','launch_identity','stop'));""")
