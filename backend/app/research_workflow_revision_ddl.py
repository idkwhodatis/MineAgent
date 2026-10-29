"""Offline028 assembly in dependency order, never a runtime migration import.

Literal installation is a separate reviewed gate. Every input family remains
append-only; downgrade refuses even standalone raw intake without a workflow.
"""

import re

from backend.app import (
    rdagent_workflow_provider_revision,
    rdagent_workflow_revision,
    rdagent_workflow_sql,
    research_input_ddl,
    research_workflow_persistence_ddl,
    research_workflow_sql,
)

ASSEMBLY_SCHEMA = "workflow028_installation"


def input_sql():
    sql = research_input_ddl.render_sql(ASSEMBLY_SCHEMA)
    sql, count = re.subn(
        r"CREATE FUNCTION (mineagent_input_\w+)\(", r"CREATE FUNCTION @SCHEMA@.\1(", sql
    )
    if count != 3:
        raise ValueError("Exact input function inventory required")
    for table in research_input_ddl.TABLES:
        old = f" ON {table} "
        if sql.count(old) != 2:
            raise ValueError("Exact input trigger inventory required")
        sql = sql.replace(old, f" ON @SCHEMA@.{table} ")
    old = "EXECUTE FUNCTION mineagent_input_"
    if sql.count(old) != 10:
        raise ValueError("Exact input trigger target inventory required")
    sql = sql.replace(old, "EXECUTE FUNCTION @SCHEMA@.mineagent_input_")
    return sql.replace(ASSEMBLY_SCHEMA, "@SCHEMA@")


def render_upgrade_sql():
    return "\n".join(
        [
            research_workflow_persistence_ddl.render_upgrade_sql(
                ASSEMBLY_SCHEMA
            ).replace(ASSEMBLY_SCHEMA, "@SCHEMA@"),
            research_workflow_sql.render_upgrade_sql(),
            input_sql(),
            rdagent_workflow_sql.render_upgrade_sql(),
            rdagent_workflow_provider_revision.render_upgrade_sql(),
            rdagent_workflow_revision.render_upgrade_sql(),
        ]
    )


def render_downgrade_sql():
    tables = research_workflow_persistence_ddl.TABLES
    guard = "DO $$ BEGIN IF " + " OR ".join(
        f"EXISTS(SELECT 1 FROM @SCHEMA@.{name})" for name in tables
    )
    guard += " OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.factor-research.v3') THEN RAISE EXCEPTION 'Retained workflow or input evidence cannot be downgraded'; END IF; END $$;"
    source = []
    for table in research_input_ddl.TABLES:
        source.extend(
            [
                f"DROP TRIGGER {table}_before ON @SCHEMA@.{table};",
                f"DROP TRIGGER {table}_consistent ON @SCHEMA@.{table};",
            ]
        )
    source.extend(
        [
            "DROP FUNCTION @SCHEMA@.mineagent_input_check();",
            "DROP FUNCTION @SCHEMA@.mineagent_input_consistent(uuid);",
            "DROP FUNCTION @SCHEMA@.mineagent_input_before();",
        ]
    )
    return "\n".join(
        [
            guard,
            rdagent_workflow_revision.render_downgrade_sql(),
            rdagent_workflow_provider_revision.render_downgrade_sql(),
            rdagent_workflow_sql.render_downgrade_sql(),
            *source,
            research_workflow_sql.render_downgrade_sql(),
            research_workflow_persistence_ddl.render_downgrade_sql(
                ASSEMBLY_SCHEMA
            ).replace(ASSEMBLY_SCHEMA, "@SCHEMA@"),
        ]
    )
