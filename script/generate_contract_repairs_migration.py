"""Freeze the explicit 027a correction without rewriting 001–027 history."""

import hashlib
from pathlib import Path

from backend.app.seed_parent_contract_repair import DOWNGRADE_SQL, UPGRADE_SQL
from backend.app.structured_authoring_repair_sql import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def document():
    # Reuse only the literal predecessor's maintenance/driver source, never its
    # SQL or live application metadata. The new functions remain fully literal.
    previous = ROOT / "backend/alembic/versions/027_rd_controller_v3.py"
    source = previous.read_bytes()
    if (
        hashlib.sha256(source).hexdigest()
        != "32191ebbb36d2ad897b0aaea1f18d82690a69a45d08a347d4f1253af0491141b"
    ):
        raise ValueError("Frozen027 migration source differs")
    tail = source.decode().split("# fmt: on", 1)[1]
    tail = tail.replace("Controller version migration", "Contract repair migration")
    tail = tail.replace(
        '        "factor_seeded_submissions",',
        '        "factor_seeded_submissions",\n        "factor_generation_starts",\n        "factor_strategy_executions",',
    )
    header = '''"""Correct seeded parent and structured nullable-authoring contracts.

Literal-only additive repair; historical 001–027 bytes and evidence unchanged.
"""

import re

from alembic import op

revision = "027a_contract_repairs"
down_revision = "027_rd_controller_v3"
branch_labels = None
depends_on = None

# fmt: off
'''
    for name, value in (
        ("UPGRADE_SQL", UPGRADE_SQL + "\n" + render_upgrade_sql()),
        ("DOWNGRADE_SQL", DOWNGRADE_SQL + "\n" + render_downgrade_sql()),
    ):
        header += "\n" + name + " = " + repr(value) + "\n"
    return header + "\n# fmt: on" + tail


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/027a_contract_repairs.py").write_text(document())
