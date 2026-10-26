"""Actual SQLAlchemy dispatch, not PostgreSQL DDL execution/acceptance.

The event consumes the command at the DBAPI boundary. JSON colons and percent
operators must reach cursor.execute(statement) with NO parameter argument.
"""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

ROOT = Path(__file__).resolve().parents[2]


def migration():
    path = ROOT / "backend/alembic/versions/018_rd_source_persistence.py"
    spec = importlib.util.spec_from_file_location("rd018_literal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("generated", [False, True])
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_frozen_ddl_reaches_no_parameter_driver_without_reinterpretation(
    generated, direction
):
    workflow = (ROOT / ".github/workflows/mineagent-adapter-contracts.yml").read_text()
    assert "tests/rdagent/test_rd_migration_literal_execution.py" in workflow
    assert "assert len(cases) == 129" in workflow
    module = migration()
    # Reproduce the hosted cause: Alembic op.execute(str) coerces to text().
    assert set(text(module.UPGRADE_SQL)._bindparams) == {"0", "1"}
    assert "%" in module.UPGRADE_SQL  # Modulo/LIKE/RAISE syntax cannot be rewritten.
    namespace = vars(module)
    if generated:
        tree = ast.parse((ROOT / "script/generate_rd_source_migration.py").read_text())
        body = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "body" for t in n.targets)
        )
        namespace = {
            "UPGRADE_SQL": module.UPGRADE_SQL,
            "DOWNGRADE_SQL": module.DOWNGRADE_SQL,
        }
        exec(compile(body, "frozen_generator_body", "exec"), namespace)  # noqa: S102 -- trusted checked-in generator template only
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            received = []
            controls = []

            @event.listens_for(engine, "do_execute_no_params")
            def capture(cursor, statement, context):
                assert context.compiled is None and context.no_parameters is True
                received.append(statement)
                return True  # No SQLite or PostgreSQL DDL is executed by this test.

            @event.listens_for(engine, "do_execute")
            def reject_parameters(*args):
                pytest.fail("Literal DDL must not enter parameterized DBAPI execution")

            namespace["op"] = SimpleNamespace(
                get_bind=lambda: connection, execute=controls.append
            )
            namespace["_schema"] = lambda: '"public"'
            namespace["_maintenance"] = lambda: controls.append("maintenance retained")
            namespace[direction]()
            expected = namespace[direction.upper() + "_SQL"].replace(
                "@SCHEMA@", '"public"'
            )
            assert received == [expected]
            assert controls == [
                "maintenance retained",
                'SET LOCAL search_path = "public", pg_catalog',
            ]
    finally:
        engine.dispose()
