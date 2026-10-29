"""Regenerate only unpublished028/029; predecessor hash pins remain fail-closed."""

import ast
import json
from pathlib import Path


def freeze(path, values):
    p = Path(path)
    s = p.read_text()
    lines = s.splitlines(keepends=True)
    spans = []
    for n in ast.parse(s).body:
        if (
            isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in values
        ):
            name = n.targets[0].id
            encoded = (
                json.dumps(values[name])
                if name == "down_revision"
                else repr(values[name])
            )
            spans.append((n.lineno - 1, n.end_lineno, name + " = " + encoded + "\n"))
    assert len(spans) == len(values)
    for a, b, v in reversed(spans):
        lines[a:b] = [v]
    p.write_text("".join(lines))


def generate():
    from backend.app.oe_structured_revision_ddl import (
        render_downgrade_sql,
        render_upgrade_sql,
        table_sql,
    )

    freeze(
        "backend/alembic/versions/028_oe_structured_search.py",
        {
            "down_revision": "027c_seeded_controller_v4",
            "TABLE_SQL": table_sql(),
            "UPGRADE_SQL": render_upgrade_sql(),
            "DOWNGRADE_SQL": render_downgrade_sql(),
        },
    )

    from backend.app import research_workflow_persistence_ddl as tables
    from backend.app import research_workflow_revision_ddl as ddl

    table = tables.render_upgrade_sql(ddl.ASSEMBLY_SCHEMA).replace(
        ddl.ASSEMBLY_SCHEMA, "@SCHEMA@"
    )
    complete = ddl.render_upgrade_sql()
    if not complete.startswith(table + "\n"):
        raise ValueError("Exact successor table envelope required")
    freeze(
        "backend/alembic/versions/029_research_workflow.py",
        {
            "TABLE_SQL": table,
            "UPGRADE_SQL": complete[len(table) + 1 :],
            "DOWNGRADE_SQL": ddl.render_downgrade_sql(),
        },
    )


if __name__ == "__main__":
    generate()
