"""Additive correction for the already-authorized seeded first generation.

No historical migration changes. Existing owner/run/parent foreign keys and
026's deferred prior-seed receipt/retained-parent checks remain authoritative.
"""

UPGRADE_SQL = """ALTER TABLE @SCHEMA@.factor_generation_starts
 DROP CONSTRAINT ck_rd_source_initial_step,
 ADD CONSTRAINT ck_rd_source_initial_step CHECK (logical_step=0);
"""

DOWNGRADE_SQL = """DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_generation_starts
           WHERE parent_factor_version_id IS NOT NULL)
 THEN RAISE EXCEPTION 'Seed parent contract downgrade refused: retained parent history';
 END IF;
END $$;
ALTER TABLE @SCHEMA@.factor_generation_starts
 DROP CONSTRAINT ck_rd_source_initial_step,
 ADD CONSTRAINT ck_rd_source_initial_step
 CHECK (logical_step=0 AND parent_factor_version_id IS NULL);
"""
