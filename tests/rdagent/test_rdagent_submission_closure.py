"""Offline literal/closure checks; real PostgreSQL tests are separate required gates."""

import importlib.util
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_submission_receipts import replay  # noqa:F401

from backend.app.rdagent_submission_backup import SCHEMA, validate_closure
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_sql import render_downgrade_sql, render_upgrade_sql


def test_frozen_023_literal_and_metadata():
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/alembic/versions/023_rd_submissions.py"
    )
    spec = importlib.util.spec_from_file_location("migration023", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        module.revision == "023_rd_submissions"
        and module.down_revision == "022_factor_analysis"
    )
    assert module.TABLE_SQL == str(
        CreateTable(FactorResearchSubmission.__table__).compile(dialect=dialect())
    )
    assert module.UPGRADE_SQL == render_upgrade_sql()
    assert module.DOWNGRADE_SQL == render_downgrade_sql()
    assert "p.objective_canonical" in module.UPGRADE_SQL
    assert "execution_attempts WHERE run_id=r.id" in module.UPGRADE_SQL
    assert "r.generation<>1" in module.UPGRADE_SQL
    assert "Submission receipts cannot be represented before023" in module.DOWNGRADE_SQL
    _assert_literal_driver_dispatch(module)


def _assert_literal_driver_dispatch(module):
    """Actual SQLAlchemy boundary, not PostgreSQL migration acceptance."""
    # Current023 does not contain JSON :0/:1 collisions, unlike018/020/022.
    # Raw execution also preserves percent operators and future literal colons.
    assert not any(
        text(sql)._bindparams
        for sql in (module.TABLE_SQL, module.UPGRADE_SQL, module.DOWNGRADE_SQL)
    )
    assert "%ROWTYPE" in module.UPGRADE_SQL
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            received, controls = [], []

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                received.append(statement)
                return True  # Consume before DBAPI; PostgreSQL runs only in real gate.

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                pytest.fail("Frozen023 DDL entered parameterized driver dispatch")

            module.op = SimpleNamespace(
                get_bind=lambda: connection, execute=controls.append
            )
            module._schema = lambda: '"public"'
            module._maintenance = lambda schema: controls.append("maintenance retained")
            for direction in ("upgrade", "downgrade"):
                received.clear()
                controls.clear()
                getattr(module, direction)()
                expected = getattr(module, direction.upper() + "_SQL").replace(
                    "@SCHEMA@", '"public"'
                )
                assert received == (
                    [module.TABLE_SQL, expected]
                    if direction == "upgrade"
                    else [expected]
                )
                assert controls[0] == "maintenance retained"
                if direction == "upgrade":
                    assert controls[1:] == [
                        'SET LOCAL search_path = "public",pg_catalog'
                    ]
                else:
                    assert controls[1:] == [
                        'LOCK TABLE "public".factor_research_submissions IN ACCESS EXCLUSIVE MODE NOWAIT',
                        'DROP TABLE "public".factor_research_submissions RESTRICT',
                    ]
    finally:
        engine.dispose()


@pytest.fixture
def cold(replay):  # noqa:F811
    r = replay
    r.row.id = uuid4()
    r.row.created_at = datetime.now(UTC)
    r.row.idempotency_key = "stable-key"
    row = jsonable_encoder(vars(r.row))
    run = jsonable_encoder(vars(r.run))
    plan = jsonable_encoder(vars(r.plan))
    closure = {
        "schema_version": SCHEMA,
        "tables": {
            "factor_research_submissions": [row],
            "outbox_events": [
                {
                    "id": str(uuid4()),
                    "run_id": str(r.run.id),
                    "generation": 1,
                    "created_at": row["created_at"],
                    "dispatched_at": None,
                    "job_id": None,
                }
            ],
        },
    }
    args = {
        "rd_source_closure": {"external_bindings": {"runs": [run]}},
        "direct_analysis_closure": {"tables": {"factor_analysis_plans": [plan]}},
        "research_closure": {
            "tables": {
                "research_budget_controls": [
                    {
                        "run_id": str(r.run.id),
                        "owner_id": str(r.owner),
                        "limits": run["config"]["budget"],
                    }
                ]
            }
        },
        "maximum_rows": 100,
    }
    return closure, args


def test_metadata_closure_for_terminal_receipt(cold):
    validate_closure(cold[0], **cold[1])


@pytest.mark.parametrize(
    "field",
    ["request_sha256", "config_sha256", "runtime_profile_sha256", "preflight_sha256"],
)
def test_commitment_drift_rejected(cold, field):
    closure, args = deepcopy(cold)
    closure["tables"]["factor_research_submissions"][0][field] = "f" * 64
    with pytest.raises(ValueError):
        validate_closure(closure, **args)


def test_exact_outbox_missing_rejected(cold):
    closure, args = deepcopy(cold)
    closure["tables"]["outbox_events"] = []
    with pytest.raises(ValueError):
        validate_closure(closure, **args)


def test_duplicate_receipt_rejected(cold):
    closure, args = deepcopy(cold)
    row = deepcopy(closure["tables"]["factor_research_submissions"][0])
    row["id"] = str(uuid4())
    closure["tables"]["factor_research_submissions"].append(row)
    with pytest.raises(ValueError):
        validate_closure(closure, **args)


def test_field_inventory_not_silently_widened(cold):
    closure, args = deepcopy(cold)
    closure["tables"]["factor_research_submissions"][0]["authority"] = True
    with pytest.raises(ValueError):
        validate_closure(closure, **args)
