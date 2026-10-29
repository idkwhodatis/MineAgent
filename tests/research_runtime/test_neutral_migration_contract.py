"""Offline regression contracts complement, not replace, actual PostgreSQL tests."""

import ast
import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PATH = (
    Path(__file__).resolve().parents[2]
    / "backend/alembic/versions/015_neutral_primitives.py"
)


def migration():
    spec = importlib.util.spec_from_file_location("neutral_migration_contract", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_argument_immutability_does_not_rely_on_nullable_trigger_arguments():
    source = PATH.read_text()
    assert "COALESCE(TG_ARGV, ARRAY[]::text[])" in source
    assert "TG_NARGS = 0 AND NEW IS DISTINCT FROM OLD" in source


def test_downgrade_preflights_isolation_and_all_fk_parents_before_destructive_ddl():
    module = migration()
    statements = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    assert "transaction_isolation" in statements[0]
    assert "read committed" in statements[0]
    assert "pg_try_advisory_xact_lock(1413960260, 1129270340)" in statements[0]
    assert statements[1] == (
        "LOCK TABLE "
        + ", ".join((*module.TABLES, "users", "research_runs", "execution_attempts"))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert "Neutral evidence prevents destructive downgrade" in statements[2]
    assert all("DROP " not in sql for sql in statements[:3])
    assert_successor_fk_prelocks()

    # The historical015 lock test must reach015 before taking its reader lock;
    # walking from head would test unrelated successor FK removal instead.
    fixture_path = PATH.parents[3] / "tests/integration/test_neutral_migration.py"
    tree = ast.parse(fixture_path.read_text())
    helper = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "enter_neutral_stage"
    )
    calls = []
    connection = SimpleNamespace(scalar=lambda _: "015_neutral_primitives")
    engine = SimpleNamespace(
        begin=lambda: nullcontext(connection), connect=lambda: nullcontext(connection)
    )
    namespace = {
        "downgrade": lambda conn, target: calls.append((conn, target)),
        "text": lambda sql: sql,
    }
    exec(  # noqa: S102 -- exact repository fixture helper
        compile(ast.Module(body=[helper], type_ignores=[]), str(fixture_path), "exec"),
        namespace,
    )
    namespace["enter_neutral_stage"](engine)
    assert calls == [(connection, "015_neutral_primitives")]
    autouse = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "neutral_revision_before_domain_fixtures"
    )
    assert [ast.unparse(d) for d in autouse.decorator_list] == [
        "pytest.fixture(autouse=True)"
    ]
    assert [a.arg for a in autouse.args.args] == ["migrated_engine"]
    assert ast.unparse(autouse.body[0]) == "enter_neutral_stage(migrated_engine)"
    for name in (
        "test_neutral_downgrade_prelocks_every_parent_without_wait",
        "test_neutral_downgrade_rejects_stale_snapshot_and_preserves_new_evidence",
    ):
        test = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
        )
        assert not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "enter_neutral_stage"
            for n in ast.walk(test)
        )


def assert_successor_fk_prelocks():
    """Capture actual upgrade/down entrypoints; not a multi-connection PG proof."""
    import re

    class Capture:
        def __init__(self):
            self.statements = []

        def execute(self, statement, *args, **kwargs):
            self.statements.append(str(statement))
            return self

        exec_driver_sql = execute

        def get_bind(self):
            return self

        def scalar_one(self):
            return "audit_schema"

        def drop_table(self, name, *args, **kwargs):
            self.statements.append("DROP TABLE " + name)

    for path in sorted(PATH.parent.glob("0*.py")):
        if not "018" <= path.name[:3] <= "028":
            continue
        spec = importlib.util.spec_from_file_location("prelock_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sql = "\n".join(
            value
            for name, value in vars(module).items()
            if name.endswith("SQL") and isinstance(value, str)
        )
        parents = set(re.findall(r'REFERENCES\s+(?:@SCHEMA@\.)?"?([a-z_]+)', sql))
        owned = set(re.findall(r'CREATE TABLE\s+(?:@SCHEMA@\.)?"?([a-z_]+)', sql))
        for direction in ("upgrade", "downgrade"):
            capture = Capture()
            module.op = capture
            getattr(module, direction)()
            locked = set()
            for statement in capture.statements:
                if statement.startswith("LOCK TABLE "):
                    assert statement.endswith(" IN ACCESS EXCLUSIVE MODE NOWAIT")
                    locked.update(
                        name.strip().split(".")[-1].strip('"')
                        for name in statement[11:].split(" IN ACCESS", 1)[0].split(",")
                    )
                elif re.match(r"\s*(CREATE|DROP|ALTER)\s", statement):
                    assert parents - owned <= locked, (
                        path.name,
                        direction,
                        parents - owned - locked,
                    )
            assert parents - owned <= locked
    # Source constants and checked-in migration entrypoints remain identical.
    from backend.app.structured_revision_ddl import FK_PARENTS

    assert FK_PARENTS == ("factor_versions", "users")
