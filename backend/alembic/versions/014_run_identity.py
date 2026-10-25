"""Classify legacy runs without opening admission for unfinished engines.

The SQL predicate classifies routing identity, not financial/runtime validity.
Existing versioned configuration validators remain authoritative. Historical
config JSON and all recorded hashes are never rewritten by this migration.
"""

from alembic import op

revision = "014_run_identity"
down_revision = "013_factor_import"
branch_labels = None
depends_on = None

LEGACY_CONFIG_FUNCTION = r"""
CREATE FUNCTION mineagent_legacy_run_config(value jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT COALESCE(
    jsonb_typeof(value) = 'object' AND
    CASE WHEN value ? 'schema_version' THEN
      jsonb_typeof(value->'schema_version') = 'string' AND
      value->>'schema_version' IN (
        'tradegrad.run.v1', 'tradegrad.portable.run.v1',
        'tradegrad.final-evaluation.v1', 'tradegrad.portable.final-evaluation.v1',
        'tradegrad.independent-evaluation.v1',
        'tradegrad.portable.independent-evaluation.v1', 'tradegrad.comparison.v1'
      )
    ELSE
      value ?& ARRAY['task','dataset_version_id','strategy_version_id','start_date',
        'end_date','profile','market','dataset_sha256','strategy_sha256',
        'execution_mode','is_synthetic'] AND
      value - ARRAY['task','dataset_version_id','strategy_version_id','start_date',
        'end_date','profile','market','dataset_sha256','strategy_sha256',
        'execution_mode','is_synthetic'] = '{}'::jsonb AND
      value->>'task' IN ('cn_time','us_time','cn_cross','us_cross') AND
      value->>'profile' = 'corrected_explicit_config' AND
      value->>'execution_mode' = 'seed_evaluation' AND
      value->>'market' = upper(left(value->>'task', 2)) AND
      jsonb_typeof(value->'is_synthetic') = 'boolean' AND
      jsonb_typeof(value->'dataset_sha256') = 'string' AND
      value->>'dataset_sha256' ~ '^[0-9a-f]{64}$' AND
      jsonb_typeof(value->'strategy_sha256') = 'string' AND
      value->>'strategy_sha256' ~ '^[0-9a-f]{64}$' AND
      jsonb_typeof(value->'dataset_version_id') = 'string' AND
      value->>'dataset_version_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' AND
      jsonb_typeof(value->'strategy_version_id') = 'string' AND
      value->>'strategy_version_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' AND
      jsonb_typeof(value->'start_date') = 'string' AND
      value->>'start_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND
      jsonb_typeof(value->'end_date') = 'string' AND
      value->>'end_date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND
      value->>'start_date' < value->>'end_date' AND
      (value->>'start_date')::date < (value->>'end_date')::date
    END, false)
$$;
"""


def input_protection(*, include_identity: bool) -> str:
    identity = (
        "OR NEW.purpose IS DISTINCT FROM OLD.purpose "
        "OR NEW.engine IS DISTINCT FROM OLD.engine"
        if include_identity
        else ""
    )
    return f"""CREATE OR REPLACE FUNCTION protect_research_inputs()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.config IS DISTINCT FROM OLD.config OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
          OR NEW.dataset_version_id IS DISTINCT FROM OLD.dataset_version_id OR NEW.strategy_version_id IS DISTINCT FROM OLD.strategy_version_id
          OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256 OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
          OR (OLD.research_image_digest IS NOT NULL AND NEW.research_image_digest IS DISTINCT FROM OLD.research_image_digest)
          {identity}
        THEN RAISE EXCEPTION 'Run input provenance is immutable'; END IF;
        RETURN NEW;
      END $$;"""


def upgrade():
    # Lock before the inventory so a concurrent legacy writer cannot enter an
    # unsupported config between classification and installing the constraint.
    op.execute("LOCK TABLE research_runs IN ACCESS EXCLUSIVE MODE")
    op.execute(LEGACY_CONFIG_FUNCTION)
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM research_runs
        WHERE mineagent_legacy_run_config(config) IS NOT TRUE) THEN
        RAISE EXCEPTION 'Unsupported historical run configuration; migration refused';
      END IF;
    END $$;""")
    op.execute("""ALTER TABLE research_runs
      ADD COLUMN purpose varchar(40) NOT NULL DEFAULT 'legacy_strategy',
      ADD COLUMN engine varchar(40) NOT NULL DEFAULT 'legacy',
      ADD CONSTRAINT ck_research_run_identity CHECK
        (purpose = 'legacy_strategy' AND engine = 'legacy'),
      ADD CONSTRAINT ck_research_run_legacy_config CHECK
        (mineagent_legacy_run_config(config) IS TRUE);""")
    op.execute(input_protection(include_identity=True))


def downgrade():
    # This closed milestone admits only legacy rows, so dropping the redundant
    # labels loses no run evidence and leaves original config/hash bytes intact.
    op.execute("LOCK TABLE research_runs IN ACCESS EXCLUSIVE MODE")
    op.execute(input_protection(include_identity=False))
    op.execute("""ALTER TABLE research_runs
      DROP CONSTRAINT ck_research_run_legacy_config,
      DROP CONSTRAINT ck_research_run_identity,
      DROP COLUMN purpose, DROP COLUMN engine;
      DROP FUNCTION mineagent_legacy_run_config(jsonb);""")
