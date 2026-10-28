"""Offline production adapter-wheel reconstruction, not installed native Qlib proof."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from runtimes.qlib.build_runner_profile import build_adapters
from runtimes.rdagent.build_profile import wheel
from runtimes.rdagent_operational.build_profile import build as build_operational

ROOT = Path(__file__).resolve().parents[2]


def proof(family):
    return json.loads((ROOT / "runtimes" / family / "wheel-proof.json").read_text())


def build_rd_adapter(output):
    recipe = ast.parse((ROOT / "runtimes/rdagent/build_profile.py").read_text())
    inventory = next(
        ast.literal_eval(node.value)
        for node in ast.walk(recipe)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "adapter_files"
            for target in node.targets
        )
    )
    assert len(inventory) == len(set(inventory))
    files = {path: (ROOT / path).read_bytes() for path in inventory}
    return wheel(
        output,
        "mineagent_rdagent_adapter_profile",
        files,
        ["mineagent-rdagent-upstream-profile==0.1.0"],
    )


@pytest.mark.parametrize("family", ["qlib", "rdagent", "rdagent_operational"])
def test_all_shared_adapter_consumers_rebuild_twice_against_frozen_proof(
    tmp_path, family
):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    expected = proof(family)
    if family == "qlib":
        one = build_adapters(ROOT, first)
        two = build_adapters(ROOT, second)
        assert one == two == (expected["adapter"], expected["native_adapter"])
        recipe = ROOT / "runtimes/qlib/build_runner_profile.py"
    elif family == "rdagent":
        one = build_rd_adapter(first)
        two = build_rd_adapter(second)
        assert one == two == expected["adapter"]
        recipe = ROOT / "runtimes/rdagent/build_profile.py"
    else:
        assert (
            build_operational(ROOT, first)
            == build_operational(ROOT, second)
            == expected
        )
        recipe = ROOT / "runtimes/rdagent_operational/build_profile.py"
    assert expected["recipe_sha256"] == hashlib.sha256(recipe.read_bytes()).hexdigest()
    for path in first.glob("*.whl"):
        assert path.read_bytes() == (second / path.name).read_bytes()


@pytest.mark.parametrize(
    "family", ["qlib_runner", "qlib_native", "rdagent", "rdagent_operational"]
)
def test_extracted_wheel_only_contract_import_has_no_checkout_fallback(
    tmp_path, family
):
    output, installed = tmp_path / "wheels", tmp_path / "installed"
    output.mkdir()
    installed.mkdir()
    if family.startswith("qlib"):
        runner, native = build_adapters(ROOT, output)
        reports = [native if family == "qlib_native" else runner]
    else:
        reports = [build_rd_adapter(output)]
        if family == "rdagent_operational":
            reports.append(build_operational(ROOT, output)["operational"])
    for report in reports:
        with ZipFile(output / report["filename"]) as archive:
            assert all(
                not Path(name).is_absolute() and ".." not in Path(name).parts
                for name in archive.namelist()
            )
            archive.extractall(installed)
    code = """
import importlib, pathlib, socket, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
def no_network(*args, **kwargs):
    raise AssertionError("Contract import must not open a network connection")
socket.socket.connect = no_network
socket.create_connection = no_network
modules = ["packages.factors.contracts"]
if sys.argv[2] == "qlib_native":
    modules += ["packages.qlib_adapter.contracts", "packages.qlib_adapter.direct"]
else:
    modules += ["packages.rdagent_adapter.contracts", "packages.rdagent_adapter.controller_profile", "packages.rdagent_adapter.controller_limits"]
    if sys.argv[2] == "rdagent_operational":
        modules += ["packages.rdagent_adapter.operational_bootstrap", "packages.rdagent_adapter.provider_config"]
    from packages.rdagent_adapter.controller_profile import ControllerProfileSelection
    assert ControllerProfileSelection().model_dump() == {}
    assert ControllerProfileSelection(controller_profile="mineagent.rdagent-orchestrator.v3").controller_profile.endswith(".v3")
for name in modules:
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root), name
for name, module in tuple(sys.modules.items()):
    if name.startswith(("packages.", "runtimes.")) and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), name
print("wheel-only contract imports verified")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(installed), family],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "wheel-only contract imports verified"


def test_native_qlib_adapter_does_not_inherit_rd_controller_package(tmp_path):
    _runner, native = build_adapters(ROOT, tmp_path)
    assert not any(
        name.startswith("packages/rdagent_adapter/") for name in native["files"]
    )
