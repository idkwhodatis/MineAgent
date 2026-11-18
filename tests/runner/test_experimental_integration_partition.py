"""Exact additional experiment partition, never weakened native acceptance."""

import copy
import json
import os
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest

from script import experimental_integration_partition as partition
from script import sql_candidate_proof as proof


def test_experimental_partition_preserves_original_modules_and_full_collection():
    modules = partition.validate()
    partition.verify_v1_additions()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *modules,
        ],
        cwd=partition.ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(partition.ROOT)
            + os.pathsep
            + str(partition.ROOT / "tests/integration"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, "Experimental collection failed"
    partition.verify_collection(result.stdout)
    lines = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    for changed in (lines[:-1], lines + lines[:1]):
        with pytest.raises(AssertionError):
            partition.verify_collection("\n".join(changed))


def test_partition_rejects_preexisting_exclusions_missing_nodes_or_changed_gates():
    data = partition.load()
    changed = copy.deepcopy(data)
    changed["experiments"][0]["module"] = data["pre_existing_modules"][0]
    with pytest.raises(AssertionError):
        partition.validate(changed)
    inventory = json.loads(proof.MANIFEST.read_text())
    inventory["pure_canonical_cost"] = []
    with pytest.raises(AssertionError):
        partition.validate(inventory=inventory)
    workflows = {
        name: (partition.ROOT / ".github/workflows" / name).read_text()
        for name in {c["workflow"] for e in data["experiments"] for c in e["coverage"]}
    }
    name = "mineagent-sql-candidates.yml"
    workflows[name] = workflows[name].replace(
        'MINEAGENT_PURE_CANONICAL_REUSE_REQUIRED: "1"',
        'MINEAGENT_PURE_CANONICAL_REUSE_REQUIRED: "0"',
    )
    with pytest.raises(AssertionError):
        partition.validate(workflows=workflows)


def test_each_dedicated_experimental_group_requires_exact_successful_junit():
    groups = {
        c["group"]
        for e in partition.load()["experiments"]
        for c in e["coverage"]
        if c["group"] is not None
    }
    for group in groups:
        nodes = proof.inventory(group)

        def document(items):
            root = ET.Element("testsuites")
            suite = ET.SubElement(root, "testsuite", tests=str(len(items)))
            for node in items:
                path, name = node.split("::")
                ET.SubElement(
                    suite,
                    "testcase",
                    classname=path.removesuffix(".py").replace("/", "."),
                    name=name,
                    time="0",
                )
            return root

        assert len(proof.check_junit(nodes, ET.tostring(document(nodes)))) == len(nodes)
        for items in (nodes[:-1], nodes + nodes[:1]):
            with pytest.raises(AssertionError):
                proof.check_junit(nodes, ET.tostring(document(items)))
        for tag in ("failure", "error", "skipped"):
            root = document(nodes)
            ET.SubElement(root.find(".//testcase"), tag)
            with pytest.raises(AssertionError):
                proof.check_junit(nodes, ET.tostring(root))


def test_current_head_baseline_exact_inline_xml_gate_still_rejects_bad_results():
    import ast
    import textwrap

    source = (
        partition.ROOT / ".github/workflows/mineagent-workflow-populated-proof.yml"
    ).read_text()
    block = source.split(
        "      - name: Require actual current032g finite workflow baseline\n", 1
    )[1].split("      - name:", 1)[0]
    code = textwrap.dedent(
        block.split("python - <<'PYRESULT'\n", 1)[1].split("          PYRESULT", 1)[0]
    )
    checks = [node for node in ast.parse(code).body if isinstance(node, ast.Assert)]
    assert len(checks) == 3
    compiled = compile(
        ast.Module(body=checks, type_ignores=[]), "<baseline-junit-contract>", "exec"
    )
    name = "test_actual_current032g_workflow_completes_with_readonly_graph_checks"
    valid = ET.Element("testcase", name=name)
    exec(compiled, {"cases": [valid]})  # noqa: S102 - exact fixed workflow assertions
    for cases in ([], [valid, valid], [ET.Element("testcase", name="missing")]):
        with pytest.raises(AssertionError):
            exec(compiled, {"cases": cases})  # noqa: S102
    for tag in ("failure", "error", "skipped"):
        case = ET.Element("testcase", name=name)
        ET.SubElement(case, tag)
        with pytest.raises(AssertionError):
            exec(compiled, {"cases": [case]})  # noqa: S102
