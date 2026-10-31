"""Exact maintenance compatibility, not permission to execute any new work."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services import DomainError
from packages.research_runtime.process_contracts import ProcessError
from workers.research.rdagent_configured import require_controller_revision
from workers.research.rdagent_operational import require_maintained_revision

PREVIOUS = "032_normal_oe_note_pins"
OLD = "032a_rd_terminal_results"
CURRENT = "032b_oe_append_run_state"
INVALID = (
    [],
    ["032b_future"],
    ["033_safe_workflow_branches"],
    [PREVIOUS, OLD],
    [OLD, OLD],
    [OLD, CURRENT],
    [CURRENT, CURRENT],
)


def session(versions):
    return SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: versions))


def test_configured_profiles_inherit_exact_maintenance_and_reject_unknown_heads():
    for profile in (
        None,
        "mineagent.rdagent-orchestrator.v3",
        "mineagent.rdagent-orchestrator.v4",
    ):
        config = SimpleNamespace(controller_profile=profile)
        for revision in (PREVIOUS, OLD, CURRENT):
            require_controller_revision(session([revision]), config)
        for versions in INVALID:
            with pytest.raises(ProcessError, match="rd_controller_revision"):
                require_controller_revision(session(versions), config)


def test_maintained_controller_inherits_exact_maintenance_only():
    for revision in (PREVIOUS, OLD, CURRENT):
        require_maintained_revision(session([revision]))
    for versions in INVALID:
        with pytest.raises(ProcessError, match="rd_controller_revision"):
            require_maintained_revision(session(versions))


def test_every_worker_predecessor_gate_preserves_closed_maintenance_membership():
    expected = {
        "__main__.py": 1,
        "rdagent_configured.py": 3,
        "rdagent_numeric.py": 1,
        "rdagent_operational.py": 3,
        "rdagent_queue.py": 2,
        "rdagent_recovery.py": 1,
        "rdagent_seed_queue.py": 1,
        "structured_factor.py": 1,
        "oe_structured_queue.py": 1,
        "oe_structured_recovery.py": 1,
        "oe_structured_controller_recovery.py": 1,
        "oe_structured_engine_recovery.py": 1,
    }
    found = {}
    for path in Path("workers/research").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Tuple):
                continue
            if not any(
                isinstance(item, ast.List)
                and len(item.elts) == 1
                and isinstance(item.elts[0], ast.Constant)
                and item.elts[0].value == OLD
                for item in node.elts
            ):
                continue
            values = eval(
                compile(ast.Expression(node), str(path), "eval"),
                {"REVISION": "historical_base"},
            )
            assert values.count([CURRENT]) == 1
            assert values.index([CURRENT]) == values.index([OLD]) + 1
            assert [CURRENT] in values and [OLD] in values
            assert all(versions not in values for versions in INVALID)
            found[path.name] = found.get(path.name, 0) + 1
    assert found == expected


def test_oe_dispatch_maintenance_reaches_existing_binding_check():
    from workers.research.oe_structured_queue import require_dispatch_binding

    for revision in (PREVIOUS, OLD, CURRENT):
        with pytest.raises(DomainError) as caught:
            require_dispatch_binding(session([revision]), None, None, None)
        assert caught.value.code == "oe_structured_queue_binding"
    for versions in INVALID:
        with pytest.raises(DomainError) as caught:
            require_dispatch_binding(session(versions), None, None, None)
        assert caught.value.code == "runtime_schema_unavailable"


def test_workflow_tick_maintenance_reaches_only_existing_bounded_tick(monkeypatch):
    from contextlib import contextmanager

    from workers.research import __main__ as main
    from workers.research import workflow_tick

    calls = []
    monkeypatch.setattr(
        workflow_tick,
        "tick_workflows",
        lambda factory, settings, limit: calls.append(limit) or ("sentinel",),
    )
    for versions in ([OLD], [CURRENT], *INVALID):

        @contextmanager
        def factory(versions=versions):
            yield session(versions)

        before = len(calls)
        result = main.dispatch_workflow_tick(factory, object())
        if versions in ([OLD], [CURRENT]):
            assert result == ("sentinel",) and calls[-1] == 4
            assert len(calls) == before + 1
        else:
            assert result == () and len(calls) == before
