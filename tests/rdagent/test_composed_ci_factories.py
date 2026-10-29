"""Actual CI configuration constructors; no PostgreSQL/container acceptance claim."""

# ruff: noqa: F811

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_rdagent_configured_fixture import configured, module  # noqa:F401

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "mineagent.rdagent-orchestrator.v3"


def load(monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    return module(name)


@pytest.mark.parametrize(
    "name", ["rd_configured_controller_fixture", "structured_factor_fixture"]
)
def test_actual_config_factory_selects_v3_without_changing_other_budget(
    configured, monkeypatch, name
):
    _data, dataset, source, policy, root, _rows = configured
    factory = object()
    fixture = load(monkeypatch, name)
    for role in ("CANDIDATE", "NORMALIZER", "ORCHESTRATOR", "ANALYSIS"):
        monkeypatch.setenv("MINEAGENT_" + role + "_IMAGE", "sha256:" + "a" * 64)
    monkeypatch.setenv("MINEAGENT_QLIB_INSTALLED_IMAGE", "sha256:" + "b" * 64)
    captured = []

    def create(*args, **kwargs):
        assert args == (factory,)
        captured.append(kwargs)
        return "declared-only"

    monkeypatch.setattr(fixture, "create_configured_run", create)
    if name == "rd_configured_controller_fixture":
        monkeypatch.setattr(fixture, "sessionmaker", lambda *a, **k: factory)
        monkeypatch.setattr(
            fixture, "persist_configured_dataset", lambda *a: (dataset, source, policy)
        )
        fixture.prepare_configured_generation(None, NS(storage_root=root), uuid4())
    else:
        fixture._prepare_factor(
            factory, dataset, source, policy, NS(storage_root=root), uuid4(), 2
        )
    config = captured[0]["config"]
    assert config.controller_profile == PROFILE and config.max_iterations == 1
    assert config.budget.model_dump() == {
        "calls": 32,
        "tokens": 200000,
        "mutation_slots": 1,
        "evaluator_executions": 1,
        "wall_milliseconds": 300000,
        "storage_bytes": 512 * 1024**2,
        "process_slots": 4,
        "cost_microunits": 0,
    }


def test_submission_factory_requires_explicit_profile_and_preserves_legacy(
    configured, monkeypatch
):
    _, dataset, _, policy, _, _ = configured
    fixture = load(monkeypatch, "rd_submission_fixture")
    old_profile = fixture.profile(actual_images=False)
    assert "controller_profile" not in old_profile.model_dump(mode="json")
    new_profile = fixture.profile(actual_images=False, controller_profile=PROFILE)
    assert new_profile.controller_profile == PROFILE
    old = fixture.body(dataset, policy)
    new = fixture.body(dataset, policy, controller_profile=PROFILE)
    assert old.budget.wall_milliseconds == 240000
    assert new.budget.wall_milliseconds == 300000
    assert {
        k: v for k, v in old.budget.model_dump().items() if k != "wall_milliseconds"
    } == {k: v for k, v in new.budget.model_dump().items() if k != "wall_milliseconds"}
    for bad in ("mineagent.rdagent-orchestrator.v2", "unknown", True):
        with pytest.raises(ValueError):
            fixture.profile(actual_images=False, controller_profile=bad)
        with pytest.raises(ValueError):
            fixture.body(dataset, policy, controller_profile=bad)


@pytest.mark.parametrize(
    "file",
    [
        "test_configured_analysis_restore.py",
        "test_structured_factor_restore.py",
        "test_rdagent_user_flow.py",
        "test_rdagent_provider_user_flow.py",
        "test_rdagent_workflow_user_flow.py",
    ],
)
def test_new_actual_gate_explicitly_migrates_controller_capable_revision(file):
    source = (ROOT / "tests/integration" / file).read_text()
    tree = ast.parse(source)
    upgrades = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_upgrade"
    ]
    assert len(upgrades) == 1
    expected = {
        "test_rdagent_provider_user_flow.py": "028_oe_structured_search",
        "test_rdagent_workflow_user_flow.py": "029_research_workflow",
    }.get(file, "027c_seeded_controller_v4")
    assert upgrades[0].args[1].value == expected
    if "restore" in file or file == "test_rdagent_user_flow.py":
        assert 'manifest["schema_revision"] == "027c_seeded_controller_v4"' in source
        assert '"mineagent.research-restore-closure.v12"' in source
        assert '"mineagent.rd-source-restore-closure.v7"' in source


@pytest.mark.parametrize(
    "file", ["test_rdagent_user_flow.py", "test_rdagent_provider_user_flow.py"]
)
def test_normal_queues_select_server_profile_and_assert_persisted_contract(file):
    tree = ast.parse((ROOT / "tests/integration" / file).read_text())
    profiles = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "profile"
    ]
    assert profiles
    assert all(
        any(
            k.arg == "controller_profile"
            and isinstance(k.value, ast.Name)
            and k.value.id == "PROFILE"
            for k in n.keywords
        )
        for n in profiles
    )
    assertions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "assert_composed_controller"
    ]
    assert len(assertions) == 2
    if "provider" in file:
        floors = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "provider_budget_floor"
        ]
        assert len(floors) == 2
        assert {
            k.value.id
            for floor in floors
            for k in floor.keywords
            if k.arg == "profile" and isinstance(k.value, ast.Name)
        } == {"PROFILE", "SEEDED_PROFILE"}
        assert any(
            k.arg == "seeded"
            and isinstance(k.value, ast.Constant)
            and k.value.value is True
            for call in assertions
            for k in call.keywords
        )


@pytest.mark.parametrize(
    "provider,seeded", [(False, False), (True, False), (True, True)]
)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "config",
        "floor",
        "admission",
        "reservation",
        "launch",
        "child_profile",
        "child_wall",
    ],
)
def test_persisted_assertion_rejects_mixed_profile_evidence(
    monkeypatch, fault, provider, seeded
):
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorControllerLaunch
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )
    from backend.app.research_process_models import ResearchProcessAdmission

    fixture = load(monkeypatch, "rd_composed_profile_fixture")
    rid, aid, bid = uuid4(), uuid4(), uuid4()
    selected = fixture.SEEDED_PROFILE if seeded else PROFILE
    wall = 180000 if seeded else 120000
    run = NS(
        config={
            "controller_profile": selected,
            "max_iterations": 1,
            "budget": {"wall_milliseconds": 360000 if seeded else 300000},
        }
    )
    if provider:
        run.config["model_binding"] = {"request_deadline_ms": 8000}
        run.config["budget"]["wall_milliseconds"] += 32 * 8000
    admission = NS(id=aid, profile=selected, reservation_id=bid)
    budget = NS(
        id=bid,
        state="settled",
        purpose="process_unit",
        run_id=rid,
        amounts={"wall_milliseconds": wall},
    )
    settled = NS(
        run_id=rid, charge={"wall_milliseconds": wall}, outcome="conservative_unknown"
    )
    child_rows = [
        NS(role=role, profile=profile, reservation_id=uuid4())
        for role, profile in (
            ("factor_cutoff_candidate", "mineagent.factor-prefix-candidate.v2"),
            ("factor_normalizer", "mineagent.factor-numeric-hdf.v1"),
            ("native_evaluator", "mineagent.qlib-direct-analysis.v1"),
        )
    ]
    child_budgets = {
        child.reservation_id: NS(
            purpose="process_unit", run_id=rid, amounts={"wall_milliseconds": 60000}
        )
        for child in child_rows
    }
    if fault == "child_profile":
        child_rows[0].profile = PROFILE
    if fault == "child_wall":
        child_budgets[child_rows[0].reservation_id].amounts["wall_milliseconds"] = (
            120000
        )
    launch = NS(
        admission_id=aid, bootstrap={"config": {"controller_profile": selected}}
    )
    if fault == "config":
        run.config["controller_profile"] = "mineagent.rdagent-orchestrator.v2"
    if fault == "floor":
        run.config["budget"]["wall_milliseconds"] = 240000
    if fault == "admission":
        admission.profile = "mineagent.rdagent-orchestrator.v2"
    if fault == "reservation":
        budget.amounts["wall_milliseconds"] = 60000
    if fault == "launch":
        launch.bootstrap["config"]["controller_profile"] = (
            "mineagent.rdagent-orchestrator.v2"
        )
    if fault == "settlement_state":
        budget.state = "held"
    if fault == "settlement_charge":
        settled.charge["wall_milliseconds"] = 120000
    if fault == "settlement_scope":
        settled.run_id = uuid4()
    if fault == "settlement_outcome":
        settled.outcome = "measured"

    class Session:
        def get(self, model, key):
            if model is ResearchBudgetReservation:
                return budget if key == bid else child_budgets[key]
            assert model is ResearchRun
            return run

        def scalars(self, query):
            return NS(
                all=lambda: child_rows,
                one=lambda: {
                    ResearchProcessAdmission: admission,
                    FactorControllerLaunch: launch,
                    ResearchBudgetSettlement: settled,
                }[query.column_descriptions[0]["entity"]],
            )

    if fault:
        with pytest.raises(AssertionError):
            fixture.assert_composed_controller(Session(), rid, seeded=seeded)
    else:
        fixture.assert_composed_controller(Session(), rid, seeded=seeded)


def test_historical_controller_gate_remains_explicit_sixty_seconds():
    from packages.rdagent_adapter.controller_limits import controller_wall_seconds

    assert controller_wall_seconds("mineagent.rdagent-orchestrator.v2") == 60
    source = (ROOT / "tests/integration/rd_source_fixture.py").read_text()
    assert "wall_milliseconds=60000" in source
    legacy = (ROOT / "tests/integration/rd_controller_fixture.py").read_text()
    assert '"controller_profile"' not in legacy


@pytest.mark.parametrize(
    "fault",
    ["settlement_state", "settlement_charge", "settlement_scope", "settlement_outcome"],
)
def test_seeded_assertion_requires_exact_conservative_settlement(monkeypatch, fault):
    test_persisted_assertion_rejects_mixed_profile_evidence(
        monkeypatch, fault, True, True
    )
