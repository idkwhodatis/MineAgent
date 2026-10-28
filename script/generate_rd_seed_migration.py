"""Freeze026 new-table DDL and explicit old-function deltas; no DB execution."""

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app import (
    rdagent_provider_revision,
    rdagent_provider_sql,
    rdagent_provider_submission_sql,
    rdagent_steer_sql,
)
from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_revision import render_downgrade_sql, render_upgrade_sql

ROOT = Path(__file__).resolve().parents[1]


def document():
    table = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=postgresql.dialect())).rstrip()
        + ";"
        for model in (FactorSeededSubmission, RDProviderTransportReceipt)
    )
    table += "\n" + rdagent_steer_sql.TABLES
    upgrade = f"{rdagent_steer_sql.UPGRADE}\n{rdagent_provider_sql.render_upgrade_sql()}\n{rdagent_provider_submission_sql.render_upgrade_sql()}\n{rdagent_provider_revision.render_upgrade_sql()}\n{render_upgrade_sql()}"
    downgrade = f"{rdagent_provider_revision.render_downgrade_sql()}\n{rdagent_provider_submission_sql.render_downgrade_sql()}\n{render_downgrade_sql()}\n{rdagent_provider_sql.render_downgrade_sql()}\n{rdagent_steer_sql.DOWNGRADE}\nDROP TABLE @SCHEMA@.rd_provider_transport_receipts;"
    header = '''"""Exact configured provider, frozen guidance and seeded first generation.

Literal-only migration; imports no application code and does not launch engines.
"""

import re

from alembic import op

revision = "026_rd_seeded_submissions"
down_revision = "025_structured_factor_strategy"
branch_labels = None
depends_on = None
'''
    for name, value in (
        ("TABLE_SQL", table),
        ("UPGRADE_SQL", upgrade),
        ("DOWNGRADE_SQL", downgrade),
    ):
        header += "\n" + name + " = " + repr(value) + "\n"
    return (
        header
        + '''

def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance(schema):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Seed migration requires quiescent maintenance'; END IF;
    END $$;""")
    tables = (
        "research_runs",
        "execution_attempts",
        "factor_analysis_plans",
        "factor_research_specs",
        "factor_research_submissions",
        "factor_controller_launches",
        "outbox_events",
        "research_budget_controls",
        "factor_generation_starts",
        "factor_source_publications",
        "factor_versions",
        "factor_objects",
        "factors",
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_objects",
        "research_object_publications",
        "factor_generation_model_operations",
        "factor_source_read_witnesses",
        "users",
    )
    op.execute(
        "LOCK TABLE "
        + ",".join(schema + "." + name for name in tables)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")


def upgrade():
    schema = _schema()
    _maintenance(schema)
    connection = op.get_bind()
    connection.exec_driver_sql(
        TABLE_SQL.replace("@SCHEMA@", schema), execution_options={"no_parameters": True}
    )
    connection.exec_driver_sql(
        UPGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )


def downgrade():
    schema = _schema()
    _maintenance(schema)
    op.execute(
        "LOCK TABLE "
        + ",".join(
            schema + "." + name
            for name in (
                "factor_seeded_submissions",
                "rd_provider_transport_receipts",
                "rd_steers",
                "rd_steer_versions",
            )
        )
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
'''
    )


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/026_rd_seeded_submissions.py").write_text(
        document()
    )
