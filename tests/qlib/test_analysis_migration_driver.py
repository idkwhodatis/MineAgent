"""Actual SQLAlchemy literal dispatch only; PostgreSQL DDL acceptance is separate."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_analysis_literals_bypass_bind_and_percent_interpolation(direction):
    path = Path(__file__).parents[2] / "backend/alembic/versions/022_factor_analysis.py"
    spec = importlib.util.spec_from_file_location("analysis022_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert text(module.CONTRACT_SQL)._bindparams
    assert text(module.ANALYSIS_PROCESS_SQL)._bindparams
    names = (
        (
            "TABLE_SQL",
            "PROFILE_UP",
            "RAW_SQL",
            "PLAN_SQL",
            "CONTRACT_SQL",
            "ANALYSIS_PROCESS_SQL",
            "ANALYSIS_SQL",
            "INPUT_UP",
            "RD_UP",
            "EXPOSURE_UP",
        )
        if direction == "upgrade"
        else ("EXPOSURE_DOWN", "RD_DOWN", "INPUT_DOWN", "PROFILE_DOWN")
    )
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            received, controls = [], []

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                received.append(statement)
                return True  # Capture before executing any DDL on SQLite.

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                pytest.fail("Frozen analysis DDL entered parameterized execution")

            module.op = SimpleNamespace(
                get_bind=lambda: connection, execute=controls.append
            )
            module._schema = lambda: '"fixture"'
            module._maintenance = lambda schema: controls.append("maintenance")
            getattr(module, direction)()
            assert controls[0] == "maintenance"
            assert received == [
                getattr(module, name).replace("@SCHEMA@", '"fixture"') for name in names
            ]
    finally:
        engine.dispose()
