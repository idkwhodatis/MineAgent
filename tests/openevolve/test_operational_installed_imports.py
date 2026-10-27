"""Actual isolated imports from the final COPY inventory; not Docker execution."""

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "runtimes/openevolve_operational/Dockerfile"


def staged_copy_inventory(target):
    final = RECIPE.read_text().rsplit("\nFROM ", 1)[1]
    for line in final.splitlines():
        if not line.startswith("COPY ") or line.startswith("COPY --"):
            continue
        sources = shlex.split(line)[1:-1]
        destination = shlex.split(line)[-1]
        if "/site-packages/" not in destination:
            continue
        relative = destination.split("/site-packages/", 1)[1]
        for source in sources:
            path = target / relative
            if destination.endswith("/"):
                path /= Path(source).name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, path)


def isolated(target, code):
    return subprocess.run(
        [sys.executable, "-I", "-c", code, str(target)],
        cwd=target,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_final_copy_inventory_imports_real_pid1_and_controller_dependencies(tmp_path):
    staged_copy_inventory(tmp_path)
    code = """
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
for name in (
    'runtimes.openevolve_operational.deadline',
    'runtimes.openevolve_operational.entrypoint',
    'runtimes.openevolve_operational.bootstrap',
    'runtimes.openevolve_operational.transport',
    'runtimes.openevolve_operational.evaluator',
    'packages.openevolve_adapter.runtime_config',
    'packages.openevolve_adapter.sampling_control',
    'packages.openevolve_adapter.placement_observer',
):
    importlib.import_module(name)
for name, module in tuple(sys.modules.items()):
    if name.startswith(('packages.', 'runtimes.')) and getattr(module, '__file__', None):
        assert pathlib.Path(module.__file__).is_relative_to(root), name
"""
    result = isolated(tmp_path, code)
    assert result.returncode == 0, result.stderr.decode()
    contract = tmp_path / "runtimes/hostile_deadline/contract.py"
    raw = contract.read_bytes()
    contract.unlink()
    broken = isolated(tmp_path, code)
    assert broken.returncode != 0
    assert b"No module named 'runtimes.hostile_deadline.contract'" in broken.stderr
    contract.write_bytes(raw)

    # Same module as the real -I -m ENTRYPOINT, with no candidate/privilege action.
    # A normal subprocess must reach, and be rejected by, the private-PID1 gate.
    entrypoint = json.loads(RECIPE.read_text().split("ENTRYPOINT ", 1)[1].strip())
    assert entrypoint == [
        "python",
        "-I",
        "-m",
        "runtimes.openevolve_operational.deadline",
    ]
    refused = isolated(
        tmp_path,
        """
import pathlib, runpy, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1])))
sys.argv = ['runtimes.openevolve_operational.deadline', '1']
runpy.run_module('runtimes.openevolve_operational.deadline', run_name='__main__')
""",
    )
    assert refused.returncode == 125
    assert refused.stdout == refused.stderr == b""

    # Every other current recipe installing the reused PID1 primitives already
    # carries this mandatory top-level import. Do not broaden their inventory.
    for recipe in (ROOT / "runtimes").rglob("Dockerfile*"):
        if not recipe.is_file() or recipe.name.endswith("dockerignore"):
            continue
        text = recipe.read_text()
        if "COPY " in text and "runtimes/hostile_deadline/deadline.py" in text:
            assert "runtimes/hostile_deadline/contract.py" in text, recipe
