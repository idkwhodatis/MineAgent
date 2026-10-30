"""Fixture wiring and closed diagnostics only; not runtime acceptance."""

import ast
import importlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_controller_profile_v4 import binding

from backend.app.rdagent_provider_preflight import provider_budget_floor

ROOT = Path(__file__).parents[1] / "integration"
PROFILE = "mineagent.rdagent-orchestrator.v3"


@pytest.mark.parametrize(
    "filename",
    ["test_oe_structured_normal_flow.py", "test_rdagent_provider_rejection.py"],
)
def test_combined_normal_flow_explicit_runtime_profile_and_server_selector(filename):
    tree = ast.parse((ROOT / filename).read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    for name in ("profile", "RDProviderServerRuntimeProfile"):
        matches = [n for n in calls if n.func.id == name]
        assert len(matches) == 1
        assert any(
            k.arg == "controller_profile" and ast.unparse(k.value) == "PROFILE"
            for k in matches[0].keywords
        )
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "rd_composed_profile_fixture"
        and any(a.name == "PROFILE" for a in n.names)
        for n in ast.walk(tree)
    )


def test_demo_budget_uses_actual_existing_composed_floor_without_other_increases(
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(ROOT))
    module = importlib.import_module("oe_structured_rd_foundation")
    from tests.openevolve.test_rd_foundation import demo

    model = binding()
    request = module.request_from_demo(
        demo(),
        NS(connection=NS(id="fixture"), public_binding=lambda owner: model),
        uuid4(),
    )
    old = provider_budget_floor(model).model_dump()
    new = request.budget.model_dump()
    assert new.pop("wall_milliseconds") - old.pop("wall_milliseconds") == 60000
    assert new == old
    assert request.budget == provider_budget_floor(model, profile=PROFILE)
    tree = ast.parse((ROOT / "test_rdagent_provider_rejection.py").read_text())
    floors = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "provider_budget_floor"
    ]
    assert len(floors) == 1
    assert any(
        k.arg == "profile" and ast.unparse(k.value) == "PROFILE"
        for k in floors[0].keywords
    )


def test_retained_diagnostic_only_emits_closed_state_and_bounded_counts(
    monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(ROOT))
    module = importlib.import_module("normal_rd_fixture_diagnostic")
    values = iter(
        [
            ["secret"] * 2,
            [],
            ["secret"] * 3,
            [
                "launch_intent",
                "launch_identity",
                "controller_child",
                "stop",
                "extraction",
            ],
        ]
    )
    queries = []

    def scalars(query):
        queries.append(query)
        return NS(all=lambda: next(values))

    session = NS(get=lambda *a: NS(sandbox_stopped=True), scalars=scalars)
    run = NS(
        id=uuid4(),
        active_attempt_id=uuid4(),
        config={"controller_profile": PROFILE, "secret": "never emit"},
        status="failed",
    )
    module.report_normal_rd_state(session, run)
    from sqlalchemy.dialects import postgresql

    assert len(queries) == 4
    for query, table, column, limit in zip(
        queries,
        (
            "factor_generation_sources",
            "factor_evaluation_outcomes",
            "research_model_operations",
            "research_process_receipts",
        ),
        ("id", "id", "id", "kind"),
        (33, 33, 33, 65),
    ):
        compiled = query.compile(dialect=postgresql.dialect())
        assert f"{table}.{column}" in str(compiled)
        assert f"{table}.run_id" in str(compiled)
        assert limit in compiled.params.values()
    value = json.loads(capsys.readouterr().out.split(":", 1)[1])
    assert value == {
        "profile": PROFILE,
        "status": "failed",
        "sandbox_stopped": True,
        "sources": 2,
        "evaluations": 0,
        "model_operations": 3,
        "stop_receipt": True,
    }
    session.get = lambda *a: (_ for _ in ()).throw(ValueError("private exception"))
    module.report_normal_rd_state(session, run)
    assert json.loads(capsys.readouterr().out.split(":", 1)[1]) == {
        "status": "diagnostic_unavailable"
    }


def test_both_actual_workflow_queues_preserve_safe_diagnostic_wiring():
    tree = ast.parse((ROOT / "test_rdagent_workflow_user_flow.py").read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert {
        ast.unparse(n.args[1]) for n in calls if n.func.id == "diagnose_rd_queue"
    } == {"rid", "second_id"}
    assert {
        ast.unparse(n.args[1]) for n in calls if n.func.id == "report_normal_rd_state"
    } == {"run", "second_run"}
