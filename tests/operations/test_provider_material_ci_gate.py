"""The generic PG gate must prove the exact differential test actually ran."""

import ast
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_provider_material_gate_requires_exact_success_without_skip():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/v1-validation.yml").read_text()
    )
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    generic = next(
        s
        for s in steps
        if s.get("name")
        == "Validate real PostgreSQL transactions and Procrastinate schema"
    )
    assert "pytest tests/integration --maxfail=1 -q" in generic["run"]
    assert (
        "--ignore=tests/integration/test_provider_material_sql_postgres.py"
        not in generic["run"]
    )
    gate = next(
        s
        for s in steps
        if s.get("name")
        == "Require real provider material differential proof without skips"
    )
    assert "if" not in gate and "continue-on-error" not in gate
    body = gate["run"].split("PYPROVIDER'\n", 1)[1].rsplit("PYPROVIDER", 1)[0]
    tree = ast.parse(body)
    assert len([n for n in tree.body if isinstance(n, ast.Assert)]) == 3
    checks = ast.Module(
        body=[n for n in tree.body if isinstance(n, ast.Assert)], type_ignores=[]
    )
    code = compile(checks, "<provider-material-required>", "exec")
    valid = ET.Element("testcase", name="test_material_matches_frozen_public_oracles")
    exec(code, {"cases": [valid]})  # noqa: S102 - checked-in assertions only
    invalid = [[], [valid, valid], [ET.Element("testcase", name="other")]]
    for tag in ("failure", "error", "skipped"):
        case = ET.Element("testcase", name=valid.attrib["name"])
        ET.SubElement(case, tag)
        invalid.append([case])
    for cases in invalid:
        with pytest.raises(AssertionError):
            exec(code, {"cases": cases})  # noqa: S102 - checked-in assertions only
