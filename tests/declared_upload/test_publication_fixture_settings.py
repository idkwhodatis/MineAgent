"""Local acceptance opt-ins, without a database, provider or socket."""

import ast
from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.declared_upload_services import (
    PUBLICATION_PEAK_BYTES,
    PUBLICATION_PEAK_FILES,
)
from backend.app.factor_services import enabled
from backend.app.services import DomainError


@pytest.mark.parametrize(
    "name",
    ["test_declared_upload_publication.py", "test_declared_upload_research_flow.py"],
)
def test_upload_acceptance_settings_are_explicit_private_copies(
    tmp_path, monkeypatch, name
):
    monkeypatch.delenv("FACTOR_LIBRARY_ENABLED", raising=False)
    original = Settings(
        database_url="postgresql://test@localhost/test",
        secret_key="x" * 32,
        storage_root=tmp_path,
    )
    assert Settings.model_fields["factor_library_enabled"].default is False
    assert original.factor_library_enabled is False
    with pytest.raises(DomainError) as error:
        enabled(original)
    assert error.value.code == "factor_library_disabled"
    tree = ast.parse((Path("tests/integration") / name).read_text())
    fixture = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "settings"
    )
    assert len(fixture.decorator_list) == 1
    assert ast.unparse(fixture.decorator_list[0]) == "pytest.fixture"
    fixture.decorator_list = []
    namespace = {}
    # Execute only this repository-owned fixture AST; no supplied input or I/O.
    exec(compile(ast.Module(body=[fixture], type_ignores=[]), name, "exec"), namespace)  # noqa: S102
    configured = namespace["settings"](original)
    assert configured is not original
    assert configured.model_dump() == original.model_dump() | {
        "factor_library_enabled": True
    }
    assert original.factor_library_enabled is False
    enabled(configured)
    assert configured.factor_owner_max_bytes > PUBLICATION_PEAK_BYTES
    assert configured.factor_installation_max_bytes > PUBLICATION_PEAK_BYTES
    assert configured.factor_max_files > PUBLICATION_PEAK_FILES


def test_independent_upload_engine_flow_explicitly_opts_in_without_global_changes():
    tree = ast.parse(
        Path("tests/integration/test_declared_upload_research_flow.py").read_text()
    )
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_upload_flow"
    )
    updates = [
        k.value
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "model_copy"
        for k in n.keywords
        if k.arg == "update" and isinstance(k.value, ast.Dict)
    ]
    matches = [
        v
        for update in updates
        for k, v in zip(update.keys, update.values)
        if isinstance(k, ast.Constant) and k.value == "factor_library_enabled"
    ]
    assert len(matches) == 1 and ast.literal_eval(matches[0]) is True
    shared = ast.parse(Path("tests/integration/conftest.py").read_text())
    settings = next(
        n
        for n in shared.body
        if isinstance(n, ast.FunctionDef) and n.name == "settings"
    )
    assert not any(
        isinstance(n, ast.keyword) and n.arg == "factor_library_enabled"
        for n in ast.walk(settings)
    )


@pytest.mark.parametrize(
    "name",
    [
        "test_rdagent_workflow_user_flow.py",
        "workflow_structured_stage_fixture.py",
        "test_oe_structured_normal_flow.py",
    ],
)
def test_workflow_and_normal_demo_publication_explicit_opt_in(
    name, tmp_path, monkeypatch
):
    monkeypatch.delenv("FACTOR_LIBRARY_ENABLED", raising=False)
    tree = ast.parse((Path("tests/integration") / name).read_text())
    updates = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
        for keyword in node.keywords
        if keyword.arg == "update" and isinstance(keyword.value, ast.Dict)
    ]
    declarations = [
        dict(zip([ast.literal_eval(k) for k in value.keys], value.values))
        for value in updates
    ]
    runtime = [value for value in declarations if "rdagent_research_enabled" in value]
    assert len(runtime) == 1
    opt_in = ast.literal_eval(runtime[0]["factor_library_enabled"])
    assert opt_in is True
    base = Settings(
        database_url="postgresql://test@localhost/test",
        secret_key="x" * 32,
        storage_root=tmp_path,
    )
    with pytest.raises(DomainError) as error:
        enabled(base)
    assert error.value.code == "factor_library_disabled"
    configured = base.model_copy(update={"factor_library_enabled": opt_in})
    enabled(configured)
    assert not base.factor_library_enabled
    assert configured.model_dump() == base.model_dump() | {
        "factor_library_enabled": True
    }
