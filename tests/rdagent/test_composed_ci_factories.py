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
    ],
)
def test_new_actual_gate_explicitly_migrates_027(file):
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
    assert upgrades[0].args[1].value == "027b_provider_material"
    if "restore" in file or file == "test_rdagent_user_flow.py":
        assert 'manifest["schema_revision"] == "027b_provider_material"' in source
        assert '"mineagent.research-restore-closure.v11"' in source
        assert '"mineagent.rd-source-restore-closure.v6"' in source


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
        assert len(floors) == 1
        assert any(
            k.arg == "profile"
            and isinstance(k.value, ast.Name)
            and k.value.id == "PROFILE"
            for k in floors[0].keywords
        )


@pytest.mark.parametrize("provider", [False, True])
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
    monkeypatch, fault, provider
):
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorControllerLaunch
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_process_models import ResearchProcessAdmission

    fixture = load(monkeypatch, "rd_composed_profile_fixture")
    rid, aid, bid = uuid4(), uuid4(), uuid4()
    run = NS(
        config={
            "controller_profile": PROFILE,
            "max_iterations": 1,
            "budget": {"wall_milliseconds": 300000},
        }
    )
    if provider:
        run.config["model_binding"] = {"request_deadline_ms": 8000}
        run.config["budget"]["wall_milliseconds"] += 32 * 8000
    admission = NS(id=aid, profile=PROFILE, reservation_id=bid)
    budget = NS(
        purpose="process_unit", run_id=rid, amounts={"wall_milliseconds": 120000}
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
    launch = NS(admission_id=aid, bootstrap={"config": {"controller_profile": PROFILE}})
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
                }[query.column_descriptions[0]["entity"]],
            )

    if fault:
        with pytest.raises(AssertionError):
            fixture.assert_composed_controller(Session(), rid)
    else:
        fixture.assert_composed_controller(Session(), rid)


def test_historical_controller_gate_remains_explicit_sixty_seconds():
    from packages.rdagent_adapter.controller_limits import controller_wall_seconds

    assert controller_wall_seconds("mineagent.rdagent-orchestrator.v2") == 60
    source = (ROOT / "tests/integration/rd_source_fixture.py").read_text()
    assert "wall_milliseconds=60000" in source
    legacy = (ROOT / "tests/integration/rd_controller_fixture.py").read_text()
    assert '"controller_profile"' not in legacy
