"""Freeze031, retaining every predecessor migration byte unchanged."""

import ast
from pathlib import Path

from backend.app.oe_workflow_ddl import render_table_sql
from backend.app.oe_workflow_revision import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def document():
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/030_declared_upload_basket.py").read_text()
    )
    values = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"EXISTING_TABLES", "NEW_TABLES"}
    }
    existing = tuple(sorted(set(values["EXISTING_TABLES"] + values["NEW_TABLES"])))
    text = '''"""031 explicit guided RD to structured OE stage.

Frozen SQL only. Physical evidence remains the bounded live/cold reader's job.
"""

import re

from alembic import op

revision = "031_workflow_structured_stage"
down_revision = "030_declared_upload_basket"
branch_labels = None
depends_on = None

# fmt: off
'''
    for name, value in [
        ("EXISTING_TABLES", existing),
        ("NEW_TABLES", ("oe_workflow_submissions",)),
        ("TABLE_SQL", render_table_sql()),
        ("UPGRADE_SQL", render_upgrade_sql()),
        ("DOWNGRADE_SQL", render_downgrade_sql()),
    ]:
        text += name + " = " + repr(value) + "\n"
    text += '''# fmt: on


def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance(schema, *, downgrade=False):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Workflow OE migration requires quiescent maintenance'; END IF;
    END $$;""")
    tables = EXISTING_TABLES + (NEW_TABLES if downgrade else ())
    op.execute(
        "LOCK TABLE "
        + ",".join(schema + "." + name for name in tables)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")


def upgrade():
    schema = _schema()
    _maintenance(schema)
    for sql in (TABLE_SQL, UPGRADE_SQL):
        op.get_bind().exec_driver_sql(
            sql.replace("@SCHEMA@", schema),
            execution_options={"no_parameters": True},
        )


def downgrade():
    schema = _schema()
    _maintenance(schema, downgrade=True)
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
'''
    return text


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/031_workflow_structured_stage.py").write_text(
        document()
    )
