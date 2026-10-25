"""Regressions for the narrowly isolated parser Docker build/import context."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = {"__init__.py", "contracts.py", "storage.py", "parser.py"}


def test_parser_context_explicit_allowlist_and_copy_sources():
    ignore = ROOT / "runtimes/factor_parser/Dockerfile.dockerignore"
    rules = [
        line.strip()
        for line in ignore.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert rules[0] == "**"
    allowed = {line[1:] for line in rules[1:]}
    assert all(line.startswith("!") for line in rules[1:])
    assert allowed == {
        "backend/",
        "backend/requirements.lock",
        "packages/",
        "packages/factors/",
        "runtimes/",
        "runtimes/factor_parser/",
        "runtimes/factor_parser/Dockerfile",
        "runtimes/factor_parser/Dockerfile.dockerignore",
        *(f"packages/factors/{name}" for name in MODULES),
    }
    assert all("*" not in path and ".." not in Path(path).parts for path in allowed)
    for path in allowed:
        assert (ROOT / path).exists()
    dockerfile = (ROOT / "runtimes/factor_parser/Dockerfile").read_text()
    assert "COPY backend/requirements.lock /tmp/requirements.lock" in dockerfile
    assert "COPY packages/factors ./packages/factors" in dockerfile
    assert "--require-hashes" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "packages.factors.parser"]' in dockerfile
    assert not any(
        "factor_parser" in line or "packages/factors" in line
        for line in (ROOT / ".dockerignore").read_text().splitlines()
    )


def test_parser_relative_import_closure_is_in_context():
    for name in MODULES:
        tree = ast.parse((ROOT / "packages/factors" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.level == 1 and node.module is not None
                assert node.module.split(".")[0] + ".py" in MODULES
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                assert not node.module.startswith(
                    ("backend", "workers", "runtimes", "packages.")
                )
    lock = (ROOT / "backend/requirements.lock").read_text()
    assert "duckdb==" in lock and "pydantic==" in lock


def test_parser_runs_with_only_allowlisted_source_modules(tmp_path):
    destination = tmp_path / "packages/factors"
    destination.mkdir(parents=True)
    for name in MODULES:
        shutil.copyfile(ROOT / "packages/factors" / name, destination / name)
    # Isolated Python ignores repo CWD/PYTHONPATH; dependency packages remain from
    # the locked API environment, exactly as the Dockerfile installs them.
    script = r"""
import sys,importlib.util
from pathlib import Path
from datetime import UTC,datetime,date
sys.path.insert(0,sys.argv[1])
assert importlib.util.find_spec('backend') is None
from packages.factors.storage import write_partition,read_page
from packages.factors.parser import parse_partition
from packages.factors.contracts import FeatureRow
assert Path(__import__('packages.factors.parser',fromlist=['x']).__file__).is_relative_to(Path(sys.argv[1]))
t=datetime(2026,1,2,21,tzinfo=UTC)
row=FeatureRow(event_time=t,session_date=date(2026,1,2),listing_id='XNAS:A',factor_version_id='00000000-0000-0000-0000-000000000001',available_at=t,value=0.0,value_state='valid',missing_reason=None,input_window_start=date(2026,1,1),input_window_end=date(2026,1,3),input_evidence_digest='a'*64,required_input_available_at=t,membership_available_at=t,lag_available_at=t)
root=Path(sys.argv[1]);(root/'output').mkdir()
source=write_partition([row],root/'input.parquet')
report=parse_partition(source.path,root/'output')
assert report['row_count']==1 and report['input_sha256']==source.sha256
assert read_page(root/'output/values.parquet',report['output_sha256'])[0]['value']==0
print('isolated-parser-source-closure-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-parser-source-closure-ok"
