"""Bounded $ref tail resolution; frozen recursion for legacy NULL/negative depth.

Only ordinary depth0..24 calls use the loop. The helper preserves the frozen
recursive algorithm, not a portable exact stack-depth/resource timing contract.
Only existing recursive edges dispatch there. No root wrapper frame is added.
No validator, graph predicate or deadline changes.
"""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_revision import changed, function

NAME = "mineagent_rd_schema"
HELPER = "mineagent_rd_schema_recursive_032e"
PIN = "166126de92db41e911154a9a108b4f4dcea32f5a7928249d16035a7ac73ef9ee"
GUARD = " IF v IS NULL OR depth>24 THEN RETURN false; END IF;\n"
REFERENCE = """ IF rule ? '$ref' THEN
   RETURN @SCHEMA@.mineagent_rd_schema(v,defs->split_part(rule->>'$ref','/',3),defs,depth+1);
 END IF;
"""
LOOP = """ WHILE rule ? '$ref' LOOP
   rule:=defs->split_part(rule->>'$ref','/',3);
   depth:=depth+1;
   IF depth>24 THEN RETURN false; END IF;
 END LOOP;
"""
RECURSIVE_CALLS = (
    "@SCHEMA@.mineagent_rd_schema(v,r,defs,depth+1)",
    "@SCHEMA@.mineagent_rd_schema(child_value,child_rule,defs,child_depth)",
)


def reference_switch():
    return (
        " IF depth IS NULL OR depth<0 THEN\n"
        + REFERENCE.replace(NAME, HELPER)
        + " ELSE\n"
        + LOOP
        + " END IF;\n"
    )


def recursive_dispatch(call):
    return (
        "(CASE WHEN depth IS NULL OR depth<0 THEN "
        + call.replace(NAME, HELPER)
        + " ELSE "
        + call
        + " END)"
    )


def schema_original():
    path = Path(__file__).parents[1] / "alembic/versions/032e_rd_workflow_cost.py"
    sql = next(
        ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in node.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PIN:
        raise ValueError("Exact frozen032e schema predecessor required")
    return function(sql, NAME)


def recursive_helper():
    return schema_original().replace(NAME, HELPER)


def schema_sql():
    sql = changed(schema_original(), REFERENCE, reference_switch())
    for call, count in zip(RECURSIVE_CALLS, (1, 2)):
        if sql.count(call) != count:
            raise ValueError("Exact frozen schema recursion inventory required")
        sql = sql.replace(call, recursive_dispatch(call))
    return sql


def render_upgrade_sql():
    return recursive_helper() + "\n" + schema_sql()


def render_downgrade_sql():
    return (
        schema_original()
        + f"\nDROP FUNCTION @SCHEMA@.{HELPER}(jsonb,jsonb,jsonb,integer);"
    )
