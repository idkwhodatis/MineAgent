"""Resolve actual acceptance ORM imports without a database or runtime."""

import ast
import importlib
from pathlib import Path
from uuid import UUID

from sqlalchemy import select


def test_all_integration_orm_imports_resolve_to_actual_models():
    checked = 0
    for path in Path("tests/integration").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("backend.app.")
                and (
                    node.module.endswith("_models")
                    or node.module == "backend.app.models"
                )
            ):
                module = importlib.import_module(node.module)
                for alias in node.names:
                    assert alias.name != "*", (path, node.lineno)
                    assert hasattr(module, alias.name), (path, node.lineno, alias.name)
                    checked += 1
    assert checked >= 230


def test_normal_rejection_retains_real_boundary_absence_and_feedback_object_queries():
    from backend.app.rdagent_boundary_models import FactorGenerationBoundary
    from backend.app.research_object_models import ResearchObject

    path = Path("tests/integration/test_rdagent_provider_rejection.py")
    tree = ast.parse(path.read_text())
    imports = {
        alias.asname or alias.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports["FactorGenerationBoundary"] == "backend.app.rdagent_boundary_models"
    assert imports["ResearchObject"] == "backend.app.research_object_models"
    assertions = [
        ast.unparse(node.test)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]
    assert (
        "not rows(FactorGenerationOutcome) and (not rows(FactorGenerationBoundary))"
        in assertions
    )
    boundary = select(FactorGenerationBoundary).filter_by(run_id=UUID(int=1))
    assert "factor_generation_boundaries.run_id" in str(boundary)
    assert FactorGenerationBoundary.__table__.c.owner_id is not None
    assert ResearchObject.__tablename__ == "research_objects"
    assert {"id", "owner_id", "storage_key", "object_kind", "sha256"} <= set(
        ResearchObject.__table__.c.keys()
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 2
        and ast.unparse(node.args[0]) == "ResearchObject"
        and ast.unparse(node.args[1]) == "failure.feedback_object_id"
        for node in ast.walk(tree)
    )
