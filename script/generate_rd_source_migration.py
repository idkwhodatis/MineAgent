import hashlib
from pathlib import Path

from backend.app import (
    rdagent_boundary_models,
    rdagent_input_models,
    rdagent_models,
    rdagent_source_witness_models,
)
from backend.app.engine_evidence_ddl import materialize
from backend.app.rdagent_canonical_sql import render_sql as canonical
from backend.app.rdagent_downgrade_sql import render_sql as downgrade
from backend.app.rdagent_identity_sql import render_sql as identity
from backend.app.rdagent_persistence_ddl import closed_phase_sql, shared_upgrade_sql
from backend.app.rdagent_shared_semantics_sql import render_sql as shared
from backend.app.rdagent_shared_semantics_sql import run_lock_sql
from backend.app.rdagent_source_sql import render_sql as source

modules = (
    rdagent_models,
    rdagent_source_witness_models,
    rdagent_boundary_models,
    rdagent_input_models,
)
names = {
    v.__table__.name
    for m in modules
    for v in vars(m).values()
    if isinstance(v, type) and v.__module__ == m.__name__ and hasattr(v, "__table__")
}
r = materialize(rdagent_models.Base.metadata, names)
domain = r["sql"].replace(
    "consumer_kind text NOT NULL CHECK",
    "consumer_kind text NOT NULL CONSTRAINT engine_object_reservation_consumers_consumer_kind_check CHECK",
)
sql = "\n".join(
    [
        canonical(),
        identity(),
        shared_upgrade_sql(),
        domain,
        run_lock_sql(names),
        shared(),
        source(),
        closed_phase_sql(),
    ]
)
sql = (
    sql.replace('"public".', "@SCHEMA@.")
    .replace("public.", "@SCHEMA@.")
    .replace("search_path = public, pg_catalog", "search_path = @SCHEMA@, pg_catalog")
)
sql = sql.replace(
    "LANGUAGE plpgsql AS $$",
    "LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$",
)
header = '''"""Initial RD source persistence only; all engine execution/default entrypoints closed.

Generated fixed SQL from reviewed explicit predicates; no runtime ORM reflection.
Local PostgreSQL execution is still required before this revision is accepted.
"""

import re
from alembic import op

revision = "018_rd_source_persistence"
down_revision = "017_numeric_process_boundary"
branch_labels = None
depends_on = None
'''
body = '''
def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance():
    op.execute("""DO $$ BEGIN
      IF current_setting('transaction_isolation') <> 'read committed'
      THEN RAISE EXCEPTION 'RD source persistence requires READ COMMITTED'; END IF;
      IF NOT pg_try_advisory_xact_lock(1413960260, 1129270340)
      THEN RAISE EXCEPTION 'RD source persistence requires quiescent maintenance'; END IF;
    END $$;""")
    op.execute("LOCK TABLE research_runs,research_model_operations,research_operation_contexts,research_budget_reservations,research_objects,research_object_publications,research_process_admissions,factor_versions,factor_objects IN ACCESS EXCLUSIVE MODE NOWAIT")


def upgrade():
    _maintenance()
    schema = _schema()
    op.execute("SET LOCAL search_path = " + schema + ", pg_catalog")
    # Frozen DDL contains JSON :0/:1 and literal percent operators. Bypass
    # SQLAlchemy bind parsing AND DBAPI parameter interpolation, unchanged bytes.
    op.get_bind().exec_driver_sql(
        UPGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )


def downgrade():
    _maintenance()
    schema = _schema()
    op.execute("SET LOCAL search_path = " + schema + ", pg_catalog")
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
'''
p = Path("backend/alembic/versions/018_rd_source_persistence.py")
p.write_text(
    header
    + "\nUPGRADE_SQL = "
    + repr(sql)
    + "\n\nDOWNGRADE_SQL = "
    + repr(
        downgrade()
        .replace('"public".', "@SCHEMA@.")
        .replace("public.", "@SCHEMA@.")
        .replace(
            "search_path = public, pg_catalog", "search_path = @SCHEMA@, pg_catalog"
        )
    )
    + "\n\nDOMAIN_TABLES = "
    + repr(tuple(r["domain_tables"]))
    + "\n"
    + body
)
print(p, len(sql), hashlib.sha256(sql.encode()).hexdigest(), len(names))
