"""Freeze additive032f; never rewrite a predecessor migration."""

import hashlib
from pathlib import Path

from backend.app import (
    oe_outbox_guard_sql,
    schema_reference_cost_sql,
    structured_raw_cost_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def document():
    source = (ROOT / "backend/alembic/versions/032e_rd_workflow_cost.py").read_bytes()
    if (
        hashlib.sha256(source).hexdigest()
        != "4b1de91e86edd98dcd8d636bdded1a09c867e55a413fdc97bb22e1afa1dcd14d"
    ):
        raise ValueError("Frozen032e migration source differs")
    # Both functions are shared across application graphs; retain every lock.
    tail = source.decode().split("# fmt: on", 1)[1]
    tail = tail.replace(
        "RD workflow cost migration", "Shared validation cost migration"
    )
    header = '''"""Reduce shared validation overhead without weakening predicates.

Frozen additive SQL. Installation requires quiescent maintenance.
"""

import re

from alembic import op

revision = "032f_shared_validation_cost"
down_revision = "032e_rd_workflow_cost"
branch_labels = None
depends_on = None

# fmt: off
'''
    renderers = (
        structured_raw_cost_sql,
        schema_reference_cost_sql,
        oe_outbox_guard_sql,
    )
    upgrade = "\n".join(module.render_upgrade_sql() for module in renderers)
    downgrade = "\n".join(
        module.render_downgrade_sql() for module in reversed(renderers)
    )
    for name, value in (("UPGRADE_SQL", upgrade), ("DOWNGRADE_SQL", downgrade)):
        header += "\n" + name + " = " + repr(value) + "\n"
    return header + "\n# fmt: on" + tail


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/032f_shared_validation_cost.py").write_text(
        document()
    )
