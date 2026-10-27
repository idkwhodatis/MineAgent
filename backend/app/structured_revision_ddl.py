"""Offline025 assembler; Alembic receives frozen literals, not live imports.

The installed024 predicates are retained as exact branches. Downgrade refuses
retained structured evidence before restoring their original bytes.
"""

import re

from backend.app.openevolve_revision_ddl import _check, _function, historical
from backend.app.structured_factor_ddl import TABLE_SQL, TABLES
from backend.app.structured_factor_raw_sql import RAW_SQL
from backend.app.structured_factor_sql import (
    GRAPH_SQL,
    TRIGGER_SQL,
    render_contract_sql,
)
from backend.app.structured_process_ddl import PROFILE_GATE, render_sql
from backend.app.structured_schema_sql import (
    JSON_VALUE_SQL,
    previous_schema_sql,
    schema_sql,
)

SHARED = (
    "research_runs",
    "execution_attempts",
    "research_budget_controls",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_process_admissions",
    "research_process_states",
    "research_process_receipts",
    "research_model_operations",
    "research_operation_contexts",
    "dataset_versions",
    "factor_execution_snapshot_publications",
    "factor_execution_snapshot_outcomes",
    "data_usage_exposures",
)
# Lock-only FK parents; do not broaden the structured graph trigger inventory.
FK_PARENTS = ("factor_versions", "users")
CHECKS = (
    (
        "research_runs",
        "ck_research_run_identity",
        "(purpose='factor_strategy' AND engine='structured')",
    ),
    (
        "research_runs",
        "ck_research_run_legacy_config",
        """(purpose='factor_strategy' AND engine='structured'
 AND strategy_version_id IS NULL AND generation=1
 AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL
 AND submission_binding IS NULL AND portable_policy_image_digest IS NULL
 AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL
 AND @SCHEMA@.mineagent_structured_contract('config',config) IS TRUE
 AND dataset_version_id::text=config->>'dataset_version_id'
 AND research_image_digest=config->>'engine_image_id')""",
    ),
    ("research_process_admissions", "ck_process_v2_unregistered", PROFILE_GATE),
)


def upgrade_sql():
    old = historical("024", "UPGRADE_SQL")
    checks = []
    for table, name, branch in CHECKS:
        checks.append(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK (({_check(old, name)} OR {branch}) IS TRUE);"
        )
    lock = _function(old, "mineagent_rd_source_run_lock")
    if "actual_engine IN ('rdagent','openevolve')" not in lock:
        raise ValueError("Exact024 engine lock branch absent")
    lock = lock.replace(
        "actual_engine IN ('rdagent','openevolve')",
        "actual_engine IN ('rdagent','openevolve','structured')",
    )
    triggers = []
    for table in TABLES:
        triggers.append(
            f"CREATE TRIGGER structured025_append BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_structured_append();"
        )
    for table in (*TABLES, *SHARED):
        triggers.append(
            f"CREATE CONSTRAINT TRIGGER structured025_consistent AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_structured_consistent();"
        )
    triggers.append(
        "CREATE TRIGGER structured025_exposure BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.data_usage_exposures FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_structured_exposure_append();"
    )
    return "\n".join(
        (
            TABLE_SQL,
            JSON_VALUE_SQL,
            schema_sql(),
            RAW_SQL,
            render_contract_sql(),
            render_sql("structured025_installation").replace(
                "structured025_installation", "@SCHEMA@"
            ),
            GRAPH_SQL,
            TRIGGER_SQL,
            *checks,
            lock,
            *triggers,
        )
    )


def downgrade_sql():
    old = historical("024", "UPGRADE_SQL")
    out = [
        "DO $$ BEGIN IF "
        + " OR ".join(f"EXISTS(SELECT 1 FROM @SCHEMA@.{table})" for table in TABLES)
        + " OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE engine='structured' OR purpose='factor_strategy')"
        + " OR EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions WHERE profile='mineagent.factor-strategy-engine.v1')"
        + " OR EXISTS(SELECT 1 FROM @SCHEMA@.data_usage_exposures WHERE purpose='structured_factor_execution_source' OR source_key LIKE 'structured-factor-%')"
        + " THEN RAISE EXCEPTION '025 retained evidence cannot be represented before025'; END IF; END $$;"
    ]
    for table in SHARED:
        out.append(f"DROP TRIGGER structured025_consistent ON @SCHEMA@.{table};")
    out.append("DROP TRIGGER structured025_exposure ON @SCHEMA@.data_usage_exposures;")
    for table, name, _ in CHECKS:
        out.append(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK {_check(old, name)};"
        )
    out.append(_function(old, "mineagent_rd_source_run_lock"))
    out.append("DROP TABLE " + ",".join("@SCHEMA@." + n for n in TABLES) + " RESTRICT;")
    out.append(previous_schema_sql())
    functions = re.findall(
        r"CREATE FUNCTION @SCHEMA@\.(mineagent_structured_\w+)\(([^)]*)\)",
        upgrade_sql(),
    )
    for name, args in reversed(functions):
        kinds = [arg.strip().split()[1] for arg in args.split(",") if arg.strip()]
        out.append(f"DROP FUNCTION @SCHEMA@.{name}({','.join(kinds)}) RESTRICT;")
    return "\n".join(out)
