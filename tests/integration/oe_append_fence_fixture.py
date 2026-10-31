"""Exact032b migration and append trigger on actual installed column projections.

No FK/producer/physical engine evidence is fabricated. This probes the complete
append fence, using real installed column types and frozen migration functions.
"""

import importlib.util
from pathlib import Path

from backend.app.oe_append_fence_repair_sql import previous_function
from backend.app.oe_structured_trigger_sql import TABLES

SCHEMA = "oe032b_append_probe"
RUN = "00000000-0000-0000-0000-000000000001"
OWNER = "00000000-0000-0000-0000-000000000002"
ATTEMPT = "00000000-0000-0000-0000-000000000003"
ROW = "00000000-0000-0000-0000-000000000004"
OTHER = "00000000-0000-0000-0000-000000000005"


def migration_statements(direction):
    path = (
        Path(__file__).parents[2]
        / "backend/alembic/versions/032b_oe_append_run_state.py"
    )
    spec = importlib.util.spec_from_file_location("oe032b_migration_fixture", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Capture:
        def __init__(self):
            self.sql = []

        def get_bind(self):
            return self

        def scalar_one(self):
            return SCHEMA

        def exec_driver_sql(self, sql, **_kwargs):
            if sql != "SELECT current_schema()":
                self.sql.append(sql)
            return self

        def execute(self, sql):
            self.sql.append(sql)

    migration.op = Capture()
    getattr(migration, direction)()
    return migration.op.sql


def insert(
    table="oe_structured_epochs", *, owner=OWNER, run=RUN, attempt=ATTEMPT, generation=1
):
    fields = "id,owner_id,run_id"
    values = f"'{ROW}','{owner}','{run}'"
    if table not in {"oe_structured_submissions", "oe_structured_specs"}:
        fields += ",attempt_id,generation"
        values += f",'{attempt}',{generation}"
    if table == "oe_structured_specs":
        fields, values = "owner_id,run_id", f"'{owner}','{run}'"
    return f"INSERT INTO {SCHEMA}.{table}({fields}) VALUES({values});"


def rejected(sql, message, state="P0001"):
    return (
        "DO $case$ BEGIN BEGIN "
        + sql
        + " RAISE EXCEPTION 'Append fence unexpectedly accepted'; EXCEPTION WHEN SQLSTATE '"
        + state
        + "' THEN IF SQLERRM NOT LIKE '"
        + message.replace("'", "''")
        + "' THEN RAISE; END IF; END; END $case$;"
    )


def setup():
    sql = [f"CREATE SCHEMA {SCHEMA};"]
    for table in ("research_runs", "execution_attempts", *TABLES):
        sql.append(
            f"CREATE TABLE {SCHEMA}.{table} AS SELECT * FROM {table} WITH NO DATA;"
        )
    sql += [
        previous_function().replace("@SCHEMA@", '"' + SCHEMA + '"'),
        f"CREATE TEMP TABLE oe032b_previous AS SELECT pg_get_functiondef('{SCHEMA}.mineagent_oes_append()'::regprocedure) definition;",
        f"DO $$ BEGIN IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='{SCHEMA}' AND table_name='research_runs' AND column_name='finished_at') THEN RAISE EXCEPTION 'Unexpected run timestamp'; END IF; END $$;",
    ]
    for table in TABLES:
        sql.append(
            f"CREATE TRIGGER actual_append BEFORE INSERT OR UPDATE OR DELETE ON {SCHEMA}.{table} FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.mineagent_oes_append();"
        )
    sql += [
        f"INSERT INTO {SCHEMA}.research_runs(id,owner_id,purpose,engine,generation,config,status,active_attempt_id) VALUES('{RUN}','{OWNER}','strategy_search','openevolve',1,'{{\"schema_version\":\"mineagent.oe-structured-provider-run.v1\"}}','running','{ATTEMPT}');",
        f"INSERT INTO {SCHEMA}.execution_attempts(id,run_id,generation,status,finished_at,lease_expires_at) VALUES('{ATTEMPT}','{RUN}',1,'running',NULL,clock_timestamp()+interval '5 minutes');",
    ]
    return sql


def statements():
    sql = setup()
    sql.append(rejected(insert(), "%finished_at%", "42703"))
    sql.extend(migration_statements("upgrade"))
    # Both inherited normal and workflow configs retain the same exact fence.
    for version in ("v1", "v2"):
        sql += [
            "SAVEPOINT matrix_case;",
            f"UPDATE {SCHEMA}.research_runs SET config=jsonb_build_object('schema_version','mineagent.oe-structured-provider-run.{version}');",
            insert(),
            "ROLLBACK TO SAVEPOINT matrix_case;",
        ]
    cases = [
        (
            "research_runs",
            "status='queued'",
            "Current structured provider run required",
        ),
        (
            "research_runs",
            "status='succeeded'",
            "Current structured provider run required",
        ),
        (
            "research_runs",
            "status='failed'",
            "Current structured provider run required",
        ),
        (
            "research_runs",
            "purpose='factor_research'",
            "Exact structured provider run required",
        ),
        ("research_runs", "engine='rdagent'", "Exact structured provider run required"),
        ("research_runs", "generation=2", "Exact structured provider run required"),
        (
            "research_runs",
            "config='{}'::jsonb",
            "Exact structured provider run required",
        ),
        (
            "research_runs",
            "active_attempt_id=NULL",
            "Current structured provider attempt required",
        ),
        (
            "research_runs",
            f"active_attempt_id='{OTHER}'",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            "status='succeeded'",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            "generation=2",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            "finished_at=clock_timestamp()",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            "lease_expires_at=NULL",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            "lease_expires_at=clock_timestamp()-interval '1 second'",
            "Current structured provider attempt required",
        ),
        (
            "execution_attempts",
            f"run_id='{OTHER}'",
            "Current structured provider attempt required",
        ),
    ]
    for table, change, message in cases:
        sql += [
            "SAVEPOINT matrix_case;",
            f"UPDATE {SCHEMA}.{table} SET {change};",
            rejected(insert(), message),
            "ROLLBACK TO SAVEPOINT matrix_case;",
        ]
    for options, message in (
        ({"owner": OTHER}, "Exact structured provider run required"),
        ({"run": OTHER}, "Exact structured provider run required"),
        ({"attempt": OTHER}, "Current structured provider attempt required"),
        ({"generation": 2}, "Current structured provider attempt required"),
    ):
        sql.append(rejected(insert(**options), message))
    for table in ("oe_structured_submissions", "oe_structured_specs"):
        sql += [
            rejected(
                insert(table),
                "Structured declaration requires original queued submission",
            ),
            "SAVEPOINT matrix_case;",
            f"UPDATE {SCHEMA}.research_runs SET status='queued',active_attempt_id=NULL;",
            insert(table),
            "ROLLBACK TO SAVEPOINT matrix_case;",
        ]
    sql += [
        insert(),
        rejected(
            f"UPDATE {SCHEMA}.oe_structured_epochs SET generation=2;",
            "Structured search evidence is immutable",
        ),
        rejected(
            f"DELETE FROM {SCHEMA}.oe_structured_epochs;",
            "Structured search evidence is immutable",
        ),
    ]
    sql.extend(migration_statements("downgrade"))
    sql += [
        f"DO $$ BEGIN IF pg_get_functiondef('{SCHEMA}.mineagent_oes_append()'::regprocedure) IS DISTINCT FROM (SELECT definition FROM oe032b_previous) THEN RAISE EXCEPTION 'OE prior function not restored exactly'; END IF; END $$;",
        rejected(insert(), "%finished_at%", "42703"),
        "SELECT 'OE032B_APPEND_MATRIX_COMPLETE';",
    ]
    return sql


def isolation_statements():
    sql = setup()
    migration = ";\n".join(migration_statements("upgrade")).replace("'", "''")
    sql += [
        rejected(
            "EXECUTE '" + migration + "';",
            "Contract repair migration requires quiescent maintenance",
        ),
        f"DO $$ BEGIN IF pg_get_functiondef('{SCHEMA}.mineagent_oes_append()'::regprocedure) IS DISTINCT FROM (SELECT definition FROM oe032b_previous) THEN RAISE EXCEPTION 'Rejected migration changed prior function'; END IF; END $$;",
        "SELECT 'OE032B_ISOLATION_REJECTED';",
    ]
    return sql
