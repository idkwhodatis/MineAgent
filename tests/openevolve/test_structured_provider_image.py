"""Closed normal image source set, not a built/accepted Docker inventory claim."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = frozenset(
    {
        "__init__",
        "broker_client",
        "candidate",
        "checkpoint",
        "checkpoint_runtime",
        "contracts",
        "evaluator_bridge",
        "objective",
        "observer",
        "observer_transport",
        "placement_observer",
        "provider_bootstrap",
        "provider_broker_client",
        "provider_contracts",
        "provider_operation",
        "provider_prompt",
        "provider_response",
        "provider_runtime_config",
        "runtime_config",
        "runtime_profile",
        "sampling_control",
        "worker_outcome",
        "workflow_bootstrap",
        "workflow_context",
        "workflow_prompt",
    }
)


def test_normal_recipe_copies_exact_public_protocol_not_host_authority():
    recipe = (ROOT / "runtimes/openevolve_structured/Dockerfile.provider").read_text()
    line = next(
        line
        for line in recipe.splitlines()
        if line.startswith("COPY packages/openevolve_structured/")
    )
    assert {Path(value).stem for value in line.split()[1:-1]} == PUBLIC
    assert all(value.endswith(".py") for value in line.split()[1:-1])
    assert "pip install" not in recipe and "secret" not in recipe.lower()
    assert (
        'ENTRYPOINT ["python","-I","-m","runtimes.openevolve_structured.deadline"]'
        in recipe
    )
    for host in (
        "provider_operator",
        "provider_transport",
        "provider_receipts",
        "provider_accounting",
        "provider_run",
        "workflow_run",
        "workflow_host_binding",
        "host_events",
        "upload_contracts",
        "checkpoint_object_identity",
    ):
        assert host not in PUBLIC


def test_normal_build_context_exactly_matches_copied_public_modules():
    rules = (
        (ROOT / "runtimes/openevolve_structured/Dockerfile.provider.dockerignore")
        .read_text()
        .splitlines()
    )
    assert rules[0] == "**"
    found = {
        Path(rule[1:]).stem
        for rule in rules
        if rule.startswith("!packages/openevolve_structured/") and rule.endswith(".py")
    }
    assert found == PUBLIC
    assert "!packages/openevolve_structured/*.py" not in rules


def test_normal_relative_import_closure_has_no_missing_or_host_module():
    for name in PUBLIC:
        tree = ast.parse(
            (ROOT / f"packages/openevolve_structured/{name}.py").read_text()
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                assert node.module.split(".")[0] in PUBLIC, (name, node.module)


def test_old_fixture_still_excludes_normal_provider_protocol():
    rules = (
        (ROOT / "runtimes/openevolve_structured/Dockerfile.dockerignore")
        .read_text()
        .splitlines()
    )
    assert "packages/openevolve_structured/provider_*.py" in rules


def test_workflow_controller_contracts_import_from_only_declared_copy_set(tmp_path):
    """Isolated import of copied source, not a built Docker or SDK-run claim."""
    import shutil
    import subprocess
    import sys

    stage = tmp_path / "site"
    stage.mkdir()
    recipe = (ROOT / "runtimes/openevolve_structured/Dockerfile.provider").read_text()
    for line in recipe.splitlines():
        if not line.startswith("COPY "):
            continue
        for source in line.split()[1:-1]:
            path = ROOT / source
            files = [path] if path.is_file() else list(path.rglob("*.py"))
            for file in files:
                target = stage / file.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
    code = r"""
import importlib, importlib.abc, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
# Repository package roots are namespaces. Explicitly close their search path
# so this import test cannot fall back to any checkout/installed host package.
for name in ("packages", "runtimes"):
    module=importlib.import_module(name)
    assert module.__file__ is None
    module.__path__=[str(root/name)]
class Closed(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("backend", "pandas", "packages.marketdata", "packages.rdagent_adapter", "packages.openevolve_structured.workflow_run", "packages.openevolve_structured.workflow_host_binding", "packages.openevolve_structured.provider_run")):
            raise AssertionError("Controller imported host-only dependency: " + fullname)
sys.meta_path.insert(0,Closed())
from packages.openevolve_structured import workflow_context, workflow_bootstrap, workflow_prompt
from packages.research_workflow import contracts, model_inputs
for module in (workflow_context,workflow_bootstrap,workflow_prompt,contracts,model_inputs):
    assert Path(module.__file__).resolve().is_relative_to(root)
assert workflow_bootstrap.WorkflowStructuredBootstrap.model_json_schema()
assert workflow_context.WorkflowOEContext.model_json_schema()
print("closed controller workflow imports")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(stage)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "closed controller workflow imports"
