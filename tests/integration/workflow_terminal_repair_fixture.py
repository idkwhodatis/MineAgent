"""Real PG predicate differential on isolated ORM-column projections.

Not a successful producer graph: other SQL constraints and physical evidence are
not synthesized or claimed. The exact installed checkpoint and raw grammar run.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, insert, literal_column
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import research_workflow_rd_terminal as live
from backend.app.openevolve_revision_ddl import _function, historical
from backend.app.research_workflow_terminal_repair_sql import (
    previous_function,
    render_downgrade_sql,
    render_upgrade_sql,
)
from packages.factors.contracts import canonical_bytes, digest
from tests.research_workflow.test_rd_terminal import completed_graph

SCHEMA = "terminal032a_predicate_probe"


def dependencies():
    names = (
        "mineagent_rd_schema",
        "mineagent_rd_canonical_json",
        "mineagent_workflow_payload",
        "mineagent_workflow_raw",
    )
    found = {}
    root = Path(__file__).resolve().parents[2] / "backend/alembic/versions"
    for path in sorted(root.glob("*.py")):
        if path.name[:3] > "032":
            continue
        for node in ast.parse(path.read_text()).body:
            if (
                not isinstance(node, ast.Assign)
                or not isinstance(node.value, ast.Constant)
                or not isinstance(node.value.value, str)
            ):
                continue
            if not any(
                getattr(t, "id", "").endswith(("UPGRADE_SQL", "UP"))
                for t in node.targets
            ):
                continue
            sql = node.value.value
            for name in names:
                if re.search(
                    r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?" + name + r"\(",
                    sql,
                ):
                    found[name] = _function(sql, name)
    assert set(found) == set(names)
    prior = _function(
        historical("029", "UPGRADE_SQL"), "mineagent_workflow_payload"
    ).replace(
        ".mineagent_workflow_payload(", ".mineagent_workflow_payload_before_oe(", 1
    )
    return [prior.replace("@SCHEMA@", SCHEMA)] + [
        found[name].replace("@SCHEMA@", SCHEMA) for name in names
    ]


def migration_statements(direction):
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/alembic/versions/032a_rd_terminal_results.py"
    )
    spec = importlib.util.spec_from_file_location("terminal032a_migration_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Capture:
        def __init__(self):
            self.sql = []

        def get_bind(self):
            return self

        def scalar_one(self):
            return SCHEMA

        def exec_driver_sql(self, sql, **kwargs):
            if sql != "SELECT current_schema()":
                self.sql.append(sql)
            return self

        def execute(self, sql):
            self.sql.append(sql)

    capture = Capture()
    module.op = capture
    getattr(module, direction)()
    return capture.sql


def statements():
    from sqlalchemy import select

    from backend.app.models import Base, ExecutionAttempt
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
    )
    from backend.app.research_workflow_models import ResearchWorkflowStage

    patch = pytest.MonkeyPatch()
    try:
        t = completed_graph.__wrapped__(patch)
        cp = live.read_rd_terminal_in_session(
            t.session, t.owner, t.workflow, t.stage, t.settings
        )
        rows = {}

        def data(row):
            if hasattr(row, "__table__"):
                return {
                    c.name: getattr(row, c.name)
                    for c in row.__table__.columns
                    if getattr(row, c.name) is not None
                }
            return dict(vars(row))

        for model, row in t.by_model.items():
            if model.__tablename__ != "research_workflows":
                rows[model.__tablename__] = [data(row)]
        stage = rows[ResearchWorkflowStage.__tablename__][0]
        stage.update(owner_id=t.owner, workflow_id=t.workflow)
        rows["factor_workflow_submissions"][0].update(
            owner_id=t.owner, workflow_id=t.workflow, stage_id=t.stage
        )
        rows["research_workflow_revisions"] = [
            {
                "id": cp.input_revision_id,
                "owner_id": t.owner,
                "workflow_id": t.workflow,
                "inputs_sha256": cp.input_sha256,
            }
        ]
        cid = uuid4()
        rows["research_workflow_checkpoints"] = [
            {
                "id": cid,
                "owner_id": t.owner,
                "workflow_id": t.workflow,
                "stage_id": t.stage,
                "terminal_canonical": canonical_bytes(cp).decode(),
                "terminal_sha256": digest(cp),
            }
        ]
        rows["research_runs"] = [data(t.run)]
        for model in (
            ExecutionAttempt,
            ResearchProcessAdmission,
            ResearchBudgetReservation,
            ResearchBudgetSettlement,
        ):
            rows[model.__tablename__] = [
                data(r) for r in t.session.scalars(select(model))
            ]
        rows["factor_direct_analysis_outcomes"] = [data(t.session.get(None, None))]
        rows["research_process_states"] = [
            {"admission_id": a.id, "status": "stopped"} for a in t.admissions
        ]
        rows["research_process_receipts"] = [
            {
                "id": uuid4(),
                "admission_id": a.id,
                "kind": "stop",
                "payload_sha256": cp.controller_stop_sha256,
            }
            for a in t.admissions
        ]
        rows["research_budget_settlements"][0]["id"] = uuid4()
    finally:
        patch.undo()
    # These empty families are still queried by the complete original predicate.
    for name in (
        "factor_evaluation_outcomes",
        "research_model_transports",
        "rd_provider_transport_receipts",
    ):
        rows.setdefault(name, [])
    sql = [f"CREATE SCHEMA {SCHEMA}", f"SET LOCAL search_path={SCHEMA},pg_catalog"]
    metadata = MetaData(schema=SCHEMA)
    for name, records in rows.items():
        table = Base.metadata.tables[name].to_metadata(metadata, schema=SCHEMA)
        table.constraints.clear()
        for col in table.columns:
            col.nullable = True
            col.default = None
            col.server_default = None
        sql.append(str(CreateTable(table).compile(dialect=dialect())))
        for row in records:
            row = {
                k: literal_column(
                    "'"
                    + json.dumps(v, separators=(",", ":")).replace("'", "''")
                    + "'::jsonb"
                )
                if isinstance(v, (dict, list))
                else v
                for k, v in row.items()
            }
            sql.append(
                str(
                    insert(table)
                    .values(**row)
                    .compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
                )
            )
    sql.extend(dependencies())

    def install(text):
        return text.replace("@SCHEMA@", SCHEMA)

    call = f"{SCHEMA}.mineagent_workflow_checkpoint('{t.owner}','{t.workflow}','{cid}')"

    def expect(value, label):
        return f"DO $$ BEGIN IF {call} IS DISTINCT FROM {str(value).lower()} THEN RAISE EXCEPTION 'terminal032a {label}'; END IF; END $$"

    signature = (
        f"'{SCHEMA}.mineagent_workflow_checkpoint(uuid,uuid,uuid)'::regprocedure"
    )
    sql += [
        install(previous_function()),
        f"CREATE TEMP TABLE terminal032a_previous AS SELECT pg_get_functiondef({signature}) AS definition",
        expect(False, "old-rejects-modern"),
    ]
    sql += migration_statements("upgrade")
    sql += [expect(True, "modern-accepted")]
    # Downgrade must refuse retained modern checkpoints without changing the predicate.
    down = install(render_downgrade_sql()).replace("'", "''")
    sql += [
        f"DO $probe$ BEGIN BEGIN EXECUTE '{down}'; RAISE EXCEPTION 'downgrade unexpectedly accepted'; EXCEPTION WHEN raise_exception THEN IF SQLERRM <> 'Cannot downgrade retained RD terminal checkpoints' THEN RAISE; END IF; END; END $probe$",
        expect(True, "failed-down-preserves-current"),
    ]
    # A legacy-only checkpoint cannot silently become invalid during upgrade.
    sql += [
        "SAVEPOINT legacy_upgrade",
        f"INSERT INTO {SCHEMA}.factor_evaluation_outcomes(id,evaluation_id,status) SELECT id,evaluation_id,status FROM {SCHEMA}.factor_execution_evaluation_outcomes",
        f"DELETE FROM {SCHEMA}.factor_execution_evaluation_outcomes",
        install(previous_function()),
    ]
    up = install(render_upgrade_sql()).replace("'", "''")
    sql += [
        f"DO $probe$ BEGIN BEGIN EXECUTE '{up}'; RAISE EXCEPTION 'upgrade unexpectedly accepted'; EXCEPTION WHEN raise_exception THEN IF SQLERRM <> 'Retained RD terminal checkpoint is not representable after032a' THEN RAISE; END IF; END; IF pg_get_functiondef({signature}) IS DISTINCT FROM (SELECT definition FROM terminal032a_previous) THEN RAISE EXCEPTION 'upgrade rollback changed function'; END IF; END $probe$",
        "ROLLBACK TO SAVEPOINT legacy_upgrade",
        "RELEASE SAVEPOINT legacy_upgrade",
        expect(True, "restored-modern"),
    ]

    mutations = [
        (
            "factor_execution_evaluation_outcomes",
            "owner_id",
            "'00000000-0000-0000-0000-000000000099'::uuid",
        ),
        (
            "factor_execution_evaluation_outcomes",
            "run_id",
            "'00000000-0000-0000-0000-000000000099'::uuid",
        ),
        (
            "factor_execution_evaluation_outcomes",
            "attempt_id",
            "'00000000-0000-0000-0000-000000000099'::uuid",
        ),
        ("factor_execution_evaluation_outcomes", "generation", "99"),
        *[
            (
                "factor_execution_evaluation_outcomes",
                k,
                "'00000000-0000-0000-0000-000000000099'::uuid",
            )
            for k in (
                "evaluation_id",
                "generation_start_id",
                "snapshot_id",
                "controller_launch_id",
                "boundary_id",
            )
        ],
        *[
            ("factor_execution_evaluation_outcomes", k, "repeat('f',64)")
            for k in (
                "snapshot_input_sha256",
                "snapshot_output_sha256",
                "snapshot_manifest_sha256",
            )
        ],
        ("factor_generation_boundaries", "boundary", "'failed'"),
        ("factor_generation_boundaries", "next_logical_step", "1"),
        ("research_process_states", "status", "'running'"),
        ("research_budget_reservations", "state", "'held'"),
    ]
    for i, (table, field, value) in enumerate(mutations):
        sql += [
            "SAVEPOINT mutation",
            f"UPDATE {SCHEMA}.{table} SET {field}={value}",
            expect(False, f"drift-{i}"),
            "ROLLBACK TO SAVEPOINT mutation",
            "RELEASE SAVEPOINT mutation",
        ]
    for table in (
        "factor_execution_evaluation_outcomes",
        "research_process_receipts",
        "research_budget_settlements",
    ):
        sql += [
            "SAVEPOINT missing",
            f"DELETE FROM {SCHEMA}.{table}",
            expect(False, "missing-" + table),
            "ROLLBACK TO SAVEPOINT missing",
            "RELEASE SAVEPOINT missing",
        ]
    sql += [f"DELETE FROM {SCHEMA}.research_workflow_checkpoints"]
    sql += migration_statements("downgrade")
    sql += [
        f"DO $$ BEGIN IF pg_get_functiondef({signature}) IS DISTINCT FROM (SELECT definition FROM terminal032a_previous) THEN RAISE EXCEPTION 'downgrade function differs'; END IF; END $$",
        "SELECT 'TERMINAL032A_MATRIX_COMPLETE'",
    ]
    return sql
