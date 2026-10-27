"""Offline composition only; do not execute generated migration as validation.

Requires the frozen020 actor boundary and the explicit021 RD final-link literals.
It fails if those dependencies are absent; no placeholder acceptance is emitted.
"""

import ast
import re
from pathlib import Path

from backend.app.execution_snapshot_ddl import TABLE_SQL
from backend.app.execution_snapshot_sql import (
    EVALUATION_CLAIM_SQL,
    EVALUATION_SQL,
    NORMALIZER_DOWNGRADE_SQL,
    NORMALIZER_SQL,
    SNAPSHOT_SQL,
    shared_claim_sql,
)
from backend.app.normalizer_report_ddl import normalizer_report_sql
from backend.app.rdagent_execution_links_sql import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def table_qualified(sql):
    names = set(re.findall(r"(?:CREATE TABLE|REFERENCES)\s+([a-z_]+)", sql))
    pattern = re.compile(
        r"\b(" + "|".join(sorted(names, key=len, reverse=True)) + r")\b"
    )
    parts = re.split(r"('(?:''|[^'])*')", sql)
    return "".join(
        part if index % 2 else pattern.sub(r"@SCHEMA@.\1", part)
        for index, part in enumerate(parts)
    )


def prior_native():
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/019_native_hdf_storage.py").read_text()
    )
    result = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    }
    claim = result["CLAIM_SQL"].split("CREATE TRIGGER claim_native_reservation", 1)[0]
    normalizer = result["NORMALIZER_SQL"].split(
        "CREATE TRIGGER protect_factor_native_normalizer_inputs", 1
    )[0]
    return claim, normalizer.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


prior_claim, prior_normalizer = prior_native()
values = {
    "TABLE_SQL": table_qualified(TABLE_SQL),
    "NORMALIZER_RESULT_SQL": NORMALIZER_SQL,
    "NORMALIZER_DOWNGRADE_SQL": NORMALIZER_DOWNGRADE_SQL,
    "SNAPSHOT_SQL": SNAPSHOT_SQL,
    "SHARED_CLAIM_SQL": shared_claim_sql(),
    "EVALUATION_CLAIM_SQL": EVALUATION_CLAIM_SQL,
    "EVALUATION_SQL": EVALUATION_SQL,
    "REPORT_INPUT_SQL": normalizer_report_sql(),
    "RD_UPGRADE_SQL": render_upgrade_sql(),
    "RD_DOWNGRADE_SQL": render_downgrade_sql(),
    "PRIOR_CLAIM_SQL": prior_claim,
    "PRIOR_NORMALIZER_SQL": prior_normalizer,
}
if not values["RD_UPGRADE_SQL"].strip() or not values["RD_DOWNGRADE_SQL"].strip():
    raise ValueError("Closed RD generation transition literals required")
header = '''"""Actual execution snapshot publication; explicit historical closure dispatch.

Frozen literals only. No runtime ORM reflection or app-code imports.
"""
import re
from alembic import op
revision = "021_execution_snapshots"
down_revision = "020_rd_controller"
branch_labels = None
depends_on = None
'''
for name, value in values.items():
    header += "\n" + name + " = " + repr(value) + "\n"
body = '''
TABLES = ("factor_execution_snapshot_publications", "factor_execution_snapshot_partitions", "factor_execution_snapshot_objects", "factor_execution_snapshot_outcomes", "factor_execution_evaluation_outcomes", "factor_evaluation_result_consumers")
SHARED = ("research_runs", "execution_attempts", "research_process_admissions", "research_process_states", "research_process_receipts", "research_budget_reservations", "research_budget_settlements", "research_objects", "research_object_publications", "factor_source_publications", "factor_native_publications", "engine_object_reservation_consumers", "factor_native_normalizer_inputs", "factor_evaluation_starts", "factor_evaluation_outcomes", "factor_generation_boundaries", "factor_generation_outcomes", "factor_generation_observations", "factor_cutoff_executions")

def _schema():
    value=op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}",value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"'+value+'"'

def _maintenance(schema):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    THEN RAISE EXCEPTION 'Execution snapshot migration requires READ COMMITTED'; END IF;
    IF NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Execution snapshot migration requires quiescent maintenance'; END IF;
    END $$;""")
    op.execute("LOCK TABLE "+",".join(schema+"."+table for table in SHARED)+" IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("SET LOCAL search_path = "+schema+", pg_catalog")

def upgrade():
    schema=_schema();_maintenance(schema)
    op.execute(f"ALTER TABLE {schema}.factor_native_normalizer_inputs ADD CONSTRAINT uq_native_normalizer_snapshot_pair UNIQUE(publication_id,admission_id)")
    for sql in (TABLE_SQL,REPORT_INPUT_SQL,NORMALIZER_RESULT_SQL,SNAPSHOT_SQL,SHARED_CLAIM_SQL,EVALUATION_CLAIM_SQL,EVALUATION_SQL):
        op.execute(sql.replace("@SCHEMA@",schema))
    op.execute(f"INSERT INTO {schema}.factor_evaluation_result_consumers(evaluation_id,owner_id,run_id,attempt_id,generation,consumer_kind,consumer_id) SELECT evaluation_id,owner_id,run_id,attempt_id,generation,'legacy_result',id FROM {schema}.factor_evaluation_outcomes")
    for table in TABLES:
        op.execute(f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_protect_neutral()")
    for table in TABLES[:4]+("research_budget_reservations","research_budget_settlements","research_process_admissions","research_process_states","research_process_receipts"):
        op.execute(f"CREATE CONSTRAINT TRIGGER check_{table}_execution_snapshot AFTER INSERT OR UPDATE ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_execution_snapshot_consistent()")
    for table in ("factor_evaluation_outcomes","factor_execution_evaluation_outcomes"):
        op.execute(f"CREATE TRIGGER claim_execution_evaluation BEFORE INSERT ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_execution_evaluation_claim()")
    for table in ("factor_evaluation_outcomes","factor_execution_evaluation_outcomes","factor_evaluation_result_consumers"):
        op.execute(f"CREATE CONSTRAINT TRIGGER check_execution_evaluation AFTER INSERT OR UPDATE ON {schema}.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_execution_evaluation_consistent()")
    op.execute(f"CREATE CONSTRAINT TRIGGER check_execution_evaluation_snapshot AFTER INSERT ON {schema}.factor_execution_evaluation_outcomes DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_execution_evaluation_snapshot()")
    for table in ("research_runs","execution_attempts"):
        op.execute(f"CREATE TRIGGER guard_execution_snapshot_terminal BEFORE UPDATE ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_execution_snapshot_terminal()")
    op.execute(RD_UPGRADE_SQL.replace("@SCHEMA@",schema))

def downgrade():
    schema=_schema();_maintenance(schema)
    op.execute("LOCK TABLE "+",".join(schema+"."+table for table in TABLES)+" IN ACCESS EXCLUSIVE MODE NOWAIT")
    checks=" OR ".join(f"EXISTS(SELECT 1 FROM {schema}.{table})" for table in TABLES[:-1])
    checks+=f" OR EXISTS(SELECT 1 FROM {schema}.research_process_receipts WHERE payload->>'schema_version'='mineagent.numeric-process-extraction.v3')"
    checks+=f" OR EXISTS(SELECT 1 FROM {schema}.engine_object_reservation_consumers WHERE consumer_kind='execution_snapshot') OR EXISTS(SELECT 1 FROM {schema}.factor_evaluation_result_consumers WHERE consumer_kind='execution_result')"
    op.execute("DO $$ BEGIN IF "+checks+" THEN RAISE EXCEPTION 'Execution snapshot evidence prevents downgrade'; END IF; END $$;")
    op.execute(NORMALIZER_DOWNGRADE_SQL.replace("@SCHEMA@",schema))
    # RD restore literal rejects old-table outcomes unavailable to020 first.
    op.execute(RD_DOWNGRADE_SQL.replace("@SCHEMA@",schema))
    for table in ("research_budget_reservations","research_budget_settlements","research_process_admissions","research_process_states","research_process_receipts"):
        op.execute(f"DROP TRIGGER check_{table}_execution_snapshot ON {schema}.{table}")
    op.execute(f"DROP TRIGGER claim_execution_evaluation ON {schema}.factor_evaluation_outcomes")
    op.execute(f"DROP TRIGGER check_execution_evaluation ON {schema}.factor_evaluation_outcomes")
    for table in ("research_runs","execution_attempts"):
        op.execute(f"DROP TRIGGER guard_execution_snapshot_terminal ON {schema}.{table}")
    # Restore shared predicates before removing their now-empty new consumer.
    op.execute(PRIOR_CLAIM_SQL.replace("@SCHEMA@",schema))
    op.execute(PRIOR_NORMALIZER_SQL.replace("@SCHEMA@",schema))
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {schema}.{table}")
    for signature in ("mineagent_execution_evaluation_snapshot()","mineagent_execution_evaluation_consistent()","mineagent_execution_evaluation_claim()","mineagent_execution_snapshot_terminal()","mineagent_execution_snapshot_consistent()","mineagent_execution_snapshot_check(uuid)","mineagent_execution_normalizer_check(uuid,jsonb)","mineagent_execution_metadata_sha(jsonb)"):
        op.execute(f"DROP FUNCTION {schema}.{signature}")
    op.execute(f"ALTER TABLE {schema}.factor_native_normalizer_inputs DROP CONSTRAINT uq_native_normalizer_snapshot_pair")
'''
(ROOT / "backend/alembic/versions/021_execution_snapshots.py").write_text(header + body)
