"""Freeze additive032e; never rewrite a predecessor migration."""

import hashlib
from pathlib import Path

from backend.app.rdagent_workflow_cost_sql import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def document():
    source = (ROOT / "backend/alembic/versions/032d_rd_validation_cost.py").read_bytes()
    if (
        hashlib.sha256(source).hexdigest()
        != "05096635aaa656ce1c4fea1bc0c0803c61517a56c5f237bd7a0eb0e156038526"
    ):
        raise ValueError("Frozen032d migration source differs")
    # Same full quiescent table-lock inventory: no tables changed since032d.
    tail = source.decode().split("# fmt: on", 1)[1]
    tail = tail.replace("RD validation cost migration", "RD workflow cost migration")
    header = '''"""Reduce RD workflow validation overhead without weakening predicates.

Frozen additive SQL. Installation requires quiescent maintenance.
"""

import re

from alembic import op

revision = "032e_rd_workflow_cost"
down_revision = "032d_rd_validation_cost"
branch_labels = None
depends_on = None

# fmt: off
'''
    for name, value in (
        ("UPGRADE_SQL", render_upgrade_sql()),
        ("DOWNGRADE_SQL", render_downgrade_sql()),
    ):
        header += "\n" + name + " = " + repr(value) + "\n"
    return header + "\n# fmt: on" + tail


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/032e_rd_workflow_cost.py").write_text(document())
