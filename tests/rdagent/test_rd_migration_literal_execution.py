"""Actual SQLAlchemy dispatch, not PostgreSQL DDL execution/acceptance.

The event consumes the command at the DBAPI boundary. JSON colons and percent
operators must reach cursor.execute(statement) with NO parameter argument.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

from packages.research_runtime.budget_contracts import BudgetVector

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
    assert (
        "pytest tests/integration --maxfail=1 -q"
        in (ROOT / ".github/workflows/v1-validation.yml").read_text()
    )
    module = migration()
    # Exact full vector: dropping even a zero-valued dimension rejects genuine
    # reservations. Keep the same engine, purpose and scoped start-consumer guard.
    source = (ROOT / "backend/app/rdagent_shared_semantics_sql.py").read_text()
    for sql in (source, module.UPGRADE_SQL):
        amounts = re.findall(r"br.amounts<>'([^']+)'::jsonb", sql)
        assert len(amounts) == 1
        assert json.loads(amounts[0]) == BudgetVector(mutation_slots=1).model_dump()
        assert "br.purpose='engine_mutation'" in sql
        assert "rr.engine<>'rdagent'" in sql
        assert (
            "NOT EXISTS(SELECT 1 FROM factor_generation_starts st WHERE st.mutation_reservation_id=br.id"
            in sql
        )
        assert (
            "(st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)"
            in sql
        )
    # PL/pgSQL's IF delimiter reader must not consume a CASE's inner THEN.
    cases = (
        "CASE WHEN kind='integer' THEN 'number' ELSE kind END",
        "CASE WHEN m.relation='continuation' THEN 'continuation' ELSE 'chat' END",
        "CASE WHEN t.ordinal=count_transports THEN 'completed' ELSE 'failed' END",
        "CASE WHEN idx=count_members-1 THEN 'stop' ELSE 'length' END",
    )
    renderers = "\n".join(
        (ROOT / name).read_text()
        for name in (
            "backend/app/rdagent_identity_sql.py",
            "backend/app/rdagent_source_sql.py",
        )
    )
    for expression in cases:
        assert "(" + expression + ")" in module.UPGRADE_SQL
        assert "(" + expression + ")" in renderers
        assert "<>" + expression not in module.UPGRADE_SQL
        assert "IS DISTINCT FROM " + expression not in module.UPGRADE_SQL
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
            # Exercise the exact standalone fixture calls at the same DBAPI
            # boundary; a raw percent modulo is not a psycopg placeholder.
            from psycopg import ProgrammingError
            from psycopg._queries import PostgresQuery
            from psycopg.adapt import Transformer

            from backend.app import rdagent_canonical_sql, rdagent_identity_sql

            for name, renderer in (
                ("test_rdagent_canonical_postgres.py", rdagent_canonical_sql),
                ("test_rdagent_identity_postgres.py", rdagent_identity_sql),
            ):
                fixture_path = ROOT / "tests/integration" / name
                calls = [
                    node
                    for node in ast.walk(ast.parse(fixture_path.read_text()))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "exec_driver_sql"
                ]
                assert len(calls) == 1
                expression = ast.fix_missing_locations(ast.Expression(calls[0]))
                eval(  # Exact trusted fixture expression only.
                    compile(expression, str(fixture_path), "eval"),
                    {
                        "connection": connection,
                        "render_sql": renderer.render_sql,
                        "schema": "public",
                    },
                )
                statement = renderer.render_sql("public")
                assert received[-1] == statement
                PostgresQuery(Transformer()).convert(statement, None)
            with pytest.raises(ProgrammingError, match="placeholders"):
                PostgresQuery(Transformer()).convert(
                    rdagent_canonical_sql.render_sql("public"), {}
                )
    finally:
        engine.dispose()
