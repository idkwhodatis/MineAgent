"""Exact offline migration/capability contract; not PostgreSQL execution proof."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app import oe_structured_revision_ddl as ddl
from backend.app.schema_capabilities import capabilities_for

PATH = Path("backend/alembic/versions/028_oe_structured_search.py")


def literals():
    tree = ast.parse(PATH.read_text())
    return {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }


def test_literal_migration_exactly_matches_reviewed_fragments():
    values = literals()
    assert values["TABLE_SQL"] == ddl.table_sql()
    assert values["UPGRADE_SQL"] == ddl.render_upgrade_sql()
    assert values["DOWNGRADE_SQL"] == ddl.render_downgrade_sql()
    assert values["revision"] == "028_oe_structured_search"
    assert values["down_revision"] == "027c_seeded_controller_v4"
    assert len(values["NEW_TABLES"]) == 21
    assert not set(values["NEW_TABLES"]) & set(values["EXISTING_TABLES"])
    assert "%%" not in values["TABLE_SQL"]
    assert "CASCADE" not in values["DOWNGRADE_SQL"]


def test_migration_only_imports_alembic_and_standard_library():
    tree = ast.parse(PATH.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert imports == ["alembic"]
    assert [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    ] == ["re"]
    source = PATH.read_text()
    assert source.count('execution_options={"no_parameters": True}') == 3
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in source
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in source


def test_exact_successor_capability_preserves_predecessor_and_adds_mandatory_closures():
    old = capabilities_for("027a_contract_repairs")
    new = capabilities_for("028_oe_structured_search")
    changed = {k for k, v in vars(new).items() if v != getattr(old, k)}
    assert changed == {
        "research_closure_schema",
        "rd_source_closure_schema",
        "oe_structured_closure_schema",
    }
    assert new.research_closure_schema == "mineagent.research-restore-closure.v13"
    assert new.rd_source_closure_schema == "mineagent.rd-source-restore-closure.v8"
    assert (
        new.oe_structured_closure_schema == "mineagent.oe-structured-restore-closure.v1"
    )


@pytest.mark.parametrize(
    "module",
    [
        "backend.app.oe_structured_models",
        "backend.app.oe_structured_checkpoint_models",
        "backend.app.oe_structured_operational_models",
        "backend.app.oe_structured_sampling_models",
        "backend.app.oe_structured_terminal_models",
        "backend.app.rdagent_numeric_failure_models",
        "script.build_oe_structured_images",
    ],
)
def test_fresh_entrypoints_register_complete_graph_without_import_cycle(module):
    code = (
        f"import importlib; importlib.import_module({module!r}); "
        "from backend.app.models import Base; "
        "from backend.app.oe_structured_persistence_ddl import tables; "
        "assert set(tables()) <= set(Base.metadata.tables); "
        "assert len(tables()) == 21"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        timeout=30,
        capture_output=True,
        text=True,
    )
