"""Portable wiring contracts only; the enclosing required PG gate executes restore."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

PATH = Path(__file__).parents[1] / "integration/oe_structured_restore_replay.py"


def _module():
    spec = importlib.util.spec_from_file_location("normal_restore_helper", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restored_settings_remove_live_operator_authority(tmp_path):
    from sqlalchemy.engine import make_url

    from backend.app.core.config import Settings

    settings = Settings(
        database_url="postgresql://localhost/source",
        storage_root=tmp_path,
        secret_key="restore-contract-test-key-not-a-secret-0000",
    )
    restored = SimpleNamespace(url=make_url("postgresql://localhost/restored"))
    result = _module()._disabled_settings(settings, restored, tmp_path / "target")
    assert result.database_url == "postgresql://localhost/restored"
    assert result.storage_root == tmp_path / "target"
    for name in (
        "model_profiles_enabled",
        "model_synthetic_test_mode",
        "oe_structured_research_enabled",
        "oe_structured_live_provider_enabled",
        "oe_structured_synthetic_provider_enabled",
        "rdagent_research_enabled",
        "rdagent_live_provider_enabled",
        "rdagent_synthetic_provider_enabled",
        "openevolve_research_enabled",
    ):
        assert getattr(result, name) is False
    assert (
        result.model_profiles
        == result.oe_structured_provider_profiles
        == result.rdagent_provider_profiles
        == ()
    )
    assert (
        result.oe_structured_provider_python is result.rdagent_provider_python is None
    )


def test_helper_is_not_an_extra_execution_or_pytest_case():
    tree = ast.parse(PATH.read_text())
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert names == {"_disabled_settings", "_inventory", "assert_normal_restore"}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"_roundtrip", "read_result", "create_app"} <= calls
    assert (
        not {
            "execute_evaluation",
            "execute_structured_acceptance_unit",
            "submit",
            "claim_attempt",
        }
        & calls
    )


def test_replay_asserts_all_closures_and_no_new_admissions_or_exposures():
    source = PATH.read_text()
    assert 'key.endswith("_closure")' in source
    assert '"oe_structured_closure", "research_closure"' in source
    assert source.index("for path, raw in responses:") < source.index(
        "for path, raw in receipts:"
    )
    assert source.count("_inventory(restored_factory) == before_inventory") >= 3
    assert "ResearchProcessAdmission" in source and "ResearchModelOperation" in source


def test_private_integrity_denial_is_not_public_lookup_semantics():
    source = PATH.read_text()
    assert "denied.value.status_code == 409" in source
    assert "api.get(path, headers=foreign).status_code == 404" in source


def test_declared_upload_flow_reuses_actual_restore_without_extra_engine_runs():
    source = (PATH.parent / "test_declared_upload_research_flow.py").read_text()
    tree = ast.parse(source)
    flow = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_actual_declared_upload_rd_release_and_normal_structured_oe"
    )
    calls = [node for node in ast.walk(flow) if isinstance(node, ast.Call)]
    assert (
        sum(
            isinstance(c.func, ast.Name) and c.func.id == "_run_upload_flow"
            for c in calls
        )
        == 1
    )
    restore = next(
        c
        for c in calls
        if isinstance(c.func, ast.Name) and c.func.id == "assert_normal_restore"
    )
    assert (
        next(k.value.value for k in restore.keywords if k.arg == "expected_revision")
        == "030_declared_upload_basket"
    )
    assert {arg.arg for arg in flow.args.args} == {
        "drill_databases",
        "tmp_path",
        "monkeypatch",
    }
    helper = PATH.read_text()
    assert 'assert "declared_upload_closure" in keys' in helper
    assert 'assert manifest["schema_revision"] == expected_revision' in helper
