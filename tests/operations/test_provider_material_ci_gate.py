"""The generic PG gate must prove the exact differential test actually ran."""

import ast
import re
import textwrap
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run_block(source, name, *, unconditional=False):
    """Read only the checked-in step shape; reject ambiguous or relaxed gates."""
    header = "      - name: " + name
    lines = source.splitlines()
    starts = [i for i, line in enumerate(lines) if line == header]
    assert len(starts) == 1
    start = starts[0] + 1
    end = next(
        (
            i
            for i in range(start, len(lines))
            if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= 6
        ),
        len(lines),
    )
    block = lines[start:end]
    runs = [
        i
        for i, line in enumerate(block)
        if re.fullmatch(r"        run: (?:\||>-)", line)
    ]
    assert len(runs) == 1
    run = runs[0]
    if unconditional:
        assert run == 0 and block[run] == "        run: |"
    script = block[run + 1 :]
    assert script and all(
        not line.strip() or line.startswith("          ") for line in script
    )
    return textwrap.dedent("\n".join(script))


def test_provider_material_gate_requires_exact_success_without_skip():
    source = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    generic = _run_block(
        source, "Validate real PostgreSQL transactions and Procrastinate schema"
    )
    assert "pytest tests/integration --maxfail=1 -q" in generic
    assert (
        "--ignore=tests/integration/test_provider_material_sql_postgres.py"
        not in generic
    )
    name = "Require real provider material differential proof without skips"
    gate = _run_block(source, name, unconditional=True)
    header = "      - name: " + name
    for changed in (
        source + "\n" + header + "\n        run: |\n          true\n",
        source.replace(header, header + "\n        if: false"),
        source.replace(header, header + "\n        continue-on-error: true"),
        source.replace(header + "\n        run: |", header + "\n        run: >-"),
    ):
        with pytest.raises(AssertionError):
            _run_block(changed, name, unconditional=True)
    body = gate.split("PYPROVIDER'\n", 1)[1].rsplit("PYPROVIDER", 1)[0]
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


def test_seeded_v4_gate_requires_both_real_sql_tests_without_skip():
    source = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    generic = _run_block(
        source, "Validate real PostgreSQL transactions and Procrastinate schema"
    )
    assert "--ignore=tests/integration/test_seeded_controller_v4_sql.py" not in generic
    gate = _run_block(
        source,
        "Require real seeded v4 profile and parent binding proof without skips",
        unconditional=True,
    )
    body = gate.split("PYSEEDED'\n", 1)[1].rsplit("PYSEEDED", 1)[0]
    tree = ast.parse(body)
    checks = [n for n in tree.body if isinstance(n, ast.Assert)]
    assert len(checks) == 3
    code = compile(
        ast.Module(body=checks, type_ignores=[]), "<seeded-v4-required>", "exec"
    )
    names = (
        "test_seeded_v4_binding_rejects_wrong_parent_owner_and_fresh",
        "test_v4_provider_only_profile_and_exact_wall_floor",
    )
    valid = [ET.Element("testcase", name=name) for name in names]
    exec(code, {"cases": valid})  # noqa: S102 - checked-in assertions only
    invalid = [[], valid[:1], valid + valid[:1], [valid[0], valid[0]]]
    for tag in ("failure", "error", "skipped"):
        cases = [ET.Element("testcase", name=name) for name in names]
        ET.SubElement(cases[1], tag)
        invalid.append(cases)
    for cases in invalid:
        with pytest.raises(AssertionError):
            exec(code, {"cases": cases})  # noqa: S102 - checked-in assertions only
