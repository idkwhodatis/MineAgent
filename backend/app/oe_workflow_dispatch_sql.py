"""031 exact version dispatch, retaining every installed030 shared guard.

This classifier only selects the existing protected structured executor. Actual
v2 acceptance additionally requires the attachment in spec/graph validation.
"""

import re

from backend.app.oe_workflow_predecessors import predecessor

COUNTS = {
    "mineagent_oes_append": 1,
    "mineagent_oes_consistent": 1,
    "mineagent_oes_graph_check": 1,
    "mineagent_oes_receipt_extension": 1,
    "mineagent_rd_shared_consistent": 10,
    "mineagent_rd_controller_receipt_order": 1,
    "mineagent_rd_controller_consistent": 1,
    "mineagent_structured_consistent": 2,
}
OLD = "'mineagent.oe-structured-provider-run.v1'"
VERSIONS = "('mineagent.oe-structured-provider-run.v1','mineagent.oe-structured-provider-run.v2')"


def render_upgrade_sql():
    values = []
    for name, count in COUNTS.items():
        sql = predecessor(name)
        if sql.count(OLD) != count:
            raise ValueError("Exact031 shared version occurrences required")
        pattern = (
            r"((?:[a-z_]+\.)?config->>'schema_version')( IS DISTINCT FROM |=)"
            + re.escape(OLD)
        )

        def replace(match):
            expr, operator = match.groups()
            result = f"{expr} IN {VERSIONS}"
            return (
                f"({result}) IS NOT TRUE"
                if operator.strip() == "IS DISTINCT FROM"
                else result
            )

        sql, changed = re.subn(pattern, replace, sql)
        if changed != count or OLD in sql.replace(VERSIONS, ""):
            raise ValueError("Exact031 shared version splice required")
        values.append(sql)
    return "\n".join(values)


def render_downgrade_sql():
    return "\n".join(predecessor(name) for name in COUNTS)
