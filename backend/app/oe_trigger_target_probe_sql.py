"""Disposable per-event OE trigger experiment; NOT a migration or runtime fix.

Only the repeated graph call for an already-checked target in one invocation is
suppressed. OLD and NEW reverse queries, membership, isolation and NOWAIT locks
still execute in their original order. No transaction/session cache is used.

Dispatch-harness evidence does NOT prove populated-graph concurrency equivalence
or native deadline/recovery acceptance. Do not promote without those proofs.
The complete batch requires an exact032g disposable database and quiescent,
caller-owned rollback-capable maintenance; never run on an application database.
"""

import re

from backend.app import row_validation_oe_032g_base_sql as base

PREDECESSOR = "032g_schema_keyset_cost"
CHANGED = ("mineagent_oes_consistent",)


def frozen(name):
    if name not in (*CHANGED, "mineagent_oes_graph_check"):
        raise ValueError("Only reviewed031 trigger and graph sources permitted")
    sql = base.literal("031_workflow_structured_stage.py", "UPGRADE_SQL")
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION @SCHEMA@\." + re.escape(name) + r"\(", sql
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact frozen function inventory required")
    start = matches[0].start()
    return sql[start : sql.index("$$;", start) + 3].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )


def original(name=CHANGED[0]):
    if name not in CHANGED:
        raise ValueError("Only the OE trigger belongs to this experiment")
    return frozen(name)


def candidate(name=CHANGED[0]):
    sql = original(name)
    edits = (
        (
            "DECLARE versions jsonb[]; v jsonb; ids uuid[]; target uuid; rid uuid;",
            "DECLARE versions jsonb[]; v jsonb; ids uuid[]; target uuid; rid uuid; seen uuid[]:=ARRAY[]::uuid[];",
        ),
        (
            "   PERFORM @SCHEMA@.mineagent_oes_graph_check(target);",
            (
                "   IF (target=ANY(seen)) IS NOT TRUE THEN\n"
                "    PERFORM @SCHEMA@.mineagent_oes_graph_check(target);\n"
                "    seen:=array_append(seen,target);\n"
                "   END IF;"
            ),
        ),
    )
    for before, after in edits:
        if sql.count(before) != 1:
            raise ValueError("Exact reviewed trigger edit required")
        sql = sql.replace(before, after, 1)
    return sql


def render_preflight_sql(direction="upgrade"):
    if direction not in {"upgrade", "downgrade"}:
        raise ValueError("Expected upgrade or downgrade")
    checks = [
        f"""IF (SELECT array_agg(version_num ORDER BY version_num) FROM @SCHEMA@.alembic_version)
 IS DISTINCT FROM ARRAY['{PREDECESSOR}']::varchar[]
 THEN RAISE EXCEPTION 'Exact032g trigger probe schema required'; END IF;
 IF current_setting('server_encoding')<>'UTF8'
 OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
 THEN RAISE EXCEPTION 'UTF8 PostgreSQL18 probe required'; END IF;"""
    ]
    checks.extend(base.function_assertion(base.original(name)) for name in base.SOURCES)
    checks.append(base.function_assertion(frozen("mineagent_oes_graph_check")))
    checks.append(
        base.function_assertion(candidate() if direction == "downgrade" else original())
    )
    return (
        "DO $oe_trigger_probe$ BEGIN\n"
        + "\n".join(checks)
        + "\nEND $oe_trigger_probe$;"
    )


def render_candidate_sql(direction="upgrade"):
    return "\n".join(
        (
            base.render_maintenance_sql(),
            render_preflight_sql(direction),
            candidate() if direction == "upgrade" else original(),
        )
    )
