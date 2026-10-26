"""Version explicit admission persistence without registering any v2 profile.

Frozen SQL: historical migrations must not depend on changing application code.
The backfill adds constant columns, avoiding immutable-row UPDATE triggers.
"""

from alembic import op

revision = "016_process_versioning"
down_revision = "015_neutral_primitives"
branch_labels = None
depends_on = None

UPGRADE_SQL = "-- All v2 profiles remain unregistered.\n\nALTER TABLE research_process_admissions\n ADD COLUMN schema_version varchar(40) NOT NULL DEFAULT 'mineagent.process-admission.v1',\n ADD COLUMN ipc_policy varchar(20) NOT NULL DEFAULT 'host_ticket';\nALTER TABLE research_process_admissions\n ALTER COLUMN schema_version DROP DEFAULT,\n ALTER COLUMN ipc_policy DROP DEFAULT,\n ALTER COLUMN ticket_sha256 DROP NOT NULL,\n DROP CONSTRAINT ck_research_process_profile,\n DROP CONSTRAINT ck_research_process_hashes,\n ADD CONSTRAINT ck_process_versioned_grammar CHECK ((\n (schema_version = 'mineagent.process-admission.v1'\n  AND role = 'neutral_fixture' AND profile = 'neutral_fixture.v1'\n  AND ipc_policy = 'host_ticket' AND ticket_sha256 IS NOT NULL\n  AND ticket_sha256 ~ '^[a-f0-9]{64}$')\n OR\n (schema_version = 'mineagent.process-admission.v2'\n  AND generation BETWEEN 1 AND 2147483647\n  AND launch_ordinal BETWEEN 1 AND 2147483647\n  AND profile ~ '^[a-z][a-z0-9_.-]{1,59}$'\n  AND (\n   (role IN ('rd_orchestrator','openevolve_orchestrator')\n    AND ipc_policy = 'host_ticket' AND ticket_sha256 IS NOT NULL\n    AND ticket_sha256 ~ '^[a-f0-9]{64}$')\n   OR\n   (role IN ('factor_cutoff_candidate','factor_normalizer','native_evaluator')\n    AND ipc_policy = 'none' AND ticket_sha256 IS NULL)\n  ))\n) IS TRUE),\n ADD CONSTRAINT ck_process_versioned_hashes CHECK ((input_sha256 ~ '^[a-f0-9]{64}$' AND image_id ~ '^sha256:[a-f0-9]{64}$') IS TRUE),\n ADD CONSTRAINT ck_process_v2_unregistered CHECK ((schema_version = 'mineagent.process-admission.v1') IS TRUE);\n"


def maintenance():
    op.execute("""DO $$ BEGIN
      IF current_setting('transaction_isolation') <> 'read committed'
      THEN RAISE EXCEPTION 'Process versioning requires READ COMMITTED isolation'; END IF;
      IF NOT pg_try_advisory_xact_lock(1413960260, 1129270340)
      THEN RAISE EXCEPTION 'Process versioning requires quiescent maintenance admission'; END IF;
    END $$;""")
    op.execute("LOCK TABLE research_process_admissions IN ACCESS EXCLUSIVE MODE NOWAIT")


def upgrade():
    maintenance()
    op.execute(UPGRADE_SQL)


def downgrade():
    maintenance()
    # Refuse any unexpected later declaration before dropping discriminators.
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM research_process_admissions
        WHERE schema_version IS DISTINCT FROM 'mineagent.process-admission.v1'
           OR ipc_policy IS DISTINCT FROM 'host_ticket' OR ticket_sha256 IS NULL)
      THEN RAISE EXCEPTION 'Versioned process evidence prevents downgrade'; END IF;
    END $$;""")
    op.execute("""ALTER TABLE research_process_admissions
      DROP CONSTRAINT ck_process_versioned_grammar,
      DROP CONSTRAINT ck_process_versioned_hashes,
      DROP CONSTRAINT ck_process_v2_unregistered,
      ALTER COLUMN ticket_sha256 SET NOT NULL,
      DROP COLUMN schema_version, DROP COLUMN ipc_policy,
      ADD CONSTRAINT ck_research_process_profile CHECK (role = 'neutral_fixture' AND profile = 'neutral_fixture.v1'),
      ADD CONSTRAINT ck_research_process_hashes CHECK (input_sha256 ~ '^[a-f0-9]{64}$' AND ticket_sha256 ~ '^[a-f0-9]{64}$' AND image_id ~ '^sha256:[a-f0-9]{64}$');""")
