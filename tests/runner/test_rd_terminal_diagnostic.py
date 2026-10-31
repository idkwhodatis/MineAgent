"""Offline fixed-site diagnostic proof, not actual child containment acceptance."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import research_workflow_rd_terminal as service
from backend.app.services import DomainError


def module():
    path = Path(__file__).parents[1] / "integration/rd_terminal_diagnostic.py"
    spec = importlib.util.spec_from_file_location("terminal_probe", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_all_nine_fixed_sites_and_unknown_source_are_closed(tmp_path):
    diagnostic = module()
    sites = diagnostic.source_sites(service)
    assert len(sites) == 9
    assert set(sites.values()) == {
        c for values in diagnostic.CATEGORIES.values() for c in values
    }
    for (code, line), category in sites.items():
        assert (
            diagnostic.classify(sites, SimpleNamespace(f_code=code, f_lineno=line))
            == category
        )
    assert (
        diagnostic.classify(sites, SimpleNamespace(f_code=object(), f_lineno=84))
        == "other"
    )
    assert (
        diagnostic.classify(
            sites, SimpleNamespace(f_code=service._settlements.__code__, f_lineno=1)
        )
        == "other"
    )
    changed = tmp_path / "private.py"
    changed.write_text(Path(service.__file__).read_text() + "\n# private revision\n")
    assert diagnostic.source_sites(SimpleNamespace(__file__=changed)) == {}


def test_actual_gate_default_exception_and_cleanup_are_unchanged(capsys):
    diagnostic = module()
    original = service.unavailable
    session = SimpleNamespace(scalars=lambda statement: [])
    with pytest.raises(DomainError) as actual, diagnostic.diagnose_rd_terminal():
        service._settlements(session, {})
    assert service.unavailable is original
    assert actual.value.code == "workflow_waiting_containment"
    assert json.loads(
        capsys.readouterr().out.removeprefix("RD_TERMINAL_FAILURES:")
    ) == {"count": 1, "categories": ["settlement_inventory"]}
    with pytest.raises(DomainError) as default, diagnostic.diagnose_rd_terminal():
        service.unavailable()
    assert default.value.code == "workflow_child_terminal_changed"
    assert service.unavailable is original
    assert json.loads(
        capsys.readouterr().out.removeprefix("RD_TERMINAL_FAILURES:")
    ) == {"count": 0, "categories": []}
    with diagnostic.diagnose_rd_terminal():
        for _ in range(12):
            with pytest.raises(DomainError):
                service.unavailable(code="workflow_waiting_containment")
        with pytest.raises(DomainError) as private:
            service.unavailable("synthetic-private-error")
        assert private.value.code == "synthetic-private-error"
        with pytest.raises(TypeError):
            service.unavailable("a", "b")
    assert service.unavailable is original
    output = capsys.readouterr().out
    assert "private" not in output and "/" not in output
    assert json.loads(output.removeprefix("RD_TERMINAL_FAILURES:")) == {
        "count": 12,
        "categories": ["other"] * 8,
    }


def test_only_first_post_execution_drive_is_observed_without_changing_expectations():
    source = (
        Path(__file__).parents[1] / "integration/test_rdagent_workflow_user_flow.py"
    ).read_text()
    tree = ast.parse(source)
    contexts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "diagnose_rd_terminal"
            for item in node.items
        )
    ]
    assert len(contexts) == 1 and len(contexts[0].body) == 1
    body = contexts[0].body[0]
    assert isinstance(body, ast.Assert)
    assert (
        ast.unparse(body)
        == "assert drive_one(factory, settings, owner_id=user.id, workflow_id=wid).state == ('paused' if allow_feedback_release else 'feedback_allocated')"
    )
