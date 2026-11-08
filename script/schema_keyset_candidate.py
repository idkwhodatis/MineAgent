"""Isolated canonical-order key-array experiment; never wired into production.

Only the public 032f schema function's exact object-key comparison changes.
The frozen recursive fallback, all recursive edges, leaf/title validators,
exception boundaries, row reads, and deadlines remain byte-for-byte intact.

jsonb_object_keys emits the canonical JSONB key order (length then bytes),
independent of values. ARRAY subqueries compare that order directly, removing
both redundant sorts and aggregate nodes. Equal sets have equal canonical
sequences and conversely. The original C-sorted arrays' equality collation is
actually database-default (ORDER BY does not supply result collation); that
collation is always deterministic in PostgreSQL. Inputs are noncollatable jsonb.

Both absent (SQL NULL) properties and empty objects yield zero keys. ARRAY maps
both to the same empty array, matching the original NULL aggregate equality.
All nonobject errors still come from the exact same jsonb_object_keys function.
No values are copied into new JSON containers. The tempting keyvalue().key
jsonpath alternative is rejected: its temporary {key,value,id} object can exceed
a JSONB size limit when the original object does not (uncaught SQLSTATE 54000).

Reviewed PostgreSQL 18 sources: jsonfuncs.c (jsonb_object_keys), jsonb_util.c
(lengthCompareJsonbString), parse_collate.c (assign_aggregate_collations), and
pg_locale_icu.c (create_pg_locale_icu). Real PG parity/performance remains needed.
"""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_revision import function

NAME = "mineagent_rd_schema"
HELPER = "mineagent_rd_schema_recursive_032e"
PIN = "d43aa6e77092da70b15a670f13ebbffcd46baf2b82e6c37bb9b0c0b14e5e13de"
OLD = """(SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(v) key)
     IS DISTINCT FROM (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(rule->'properties') key)"""
NEW = """ARRAY(SELECT jsonb_object_keys(v))
     IS DISTINCT FROM ARRAY(SELECT jsonb_object_keys(rule->'properties'))"""


def frozen_sql():
    path = (
        Path(__file__).parents[1]
        / "backend/alembic/versions/032f_shared_validation_cost.py"
    )
    sql = next(
        ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "UPGRADE_SQL"
            for target in node.targets
        )
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PIN:
        raise ValueError("Exact published 032f predecessor required")
    return sql


def restore_schema_sql():
    return function(frozen_sql(), NAME)


def schema_sql():
    original = restore_schema_sql()
    if original.count(OLD) != 1:
        raise ValueError("Exact single 032f key comparison required")
    return original.replace(OLD, NEW)


def dependencies_sql():
    return function(frozen_sql(), HELPER)


def baseline_schema_sql():
    return (dependencies_sql() + "\n" + restore_schema_sql()).replace(
        NAME, "old_rd_schema"
    )
