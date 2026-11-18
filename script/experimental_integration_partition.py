"""Closed test-only experimental partition; never replacement acceptance.

The113 original integration modules remain in their original96 generic/17 native
partition. Only nine subsequently added experiment modules run in their exact
separate proof steps. This file performs source checks and collection, not tests.
"""

import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/runner/experimental_integration_partition.json"
MANIFEST_SHA256 = "ff90020dc5845ec4b5eb7f4439f5e3ed77adf11fb9b542e052b367318ca609a9"
OLD_COMMENT = """        # These exact runtime modules run on this same head in the dedicated
        # image-building workflows; no PostgreSQL-only module is excluded.
"""
NEW_COMMENT = """        # Original17 runtime modules retain their mandatory image-building gates.
        # Nine later experiment modules run only in separately inventoried proof
        # steps; all96 pre-existing generic PostgreSQL modules remain here.
"""


def load():
    raw = MANIFEST.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256
    return json.loads(raw)


def validate(data=None, *, workflows=None, inventory=None):
    from script import sql_candidate_proof

    data = load() if data is None else data
    old = data["pre_existing_modules"]
    entries = data["experiments"]
    modules = [entry["module"] for entry in entries]
    assert len(old) == len(set(old)) == 113
    assert len(modules) == len(set(modules)) == 9
    assert set(modules).isdisjoint(old)
    assert all((ROOT / path).is_file() for path in old + modules)
    source = (ROOT / "tests/runner/test_ci_acceptance_boundary.py").read_text()
    tree = ast.parse(source)
    helper = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_assert_actual_runtime_partition"
    )
    assert (
        hashlib.sha256(ast.get_source_segment(source, helper).encode()).hexdigest()
        == data["original_runtime_helper_sha256"]
    )
    inventory = (
        json.loads(sql_candidate_proof.MANIFEST.read_text())
        if inventory is None
        else inventory
    )
    workflows = (
        {
            name: (ROOT / ".github/workflows" / name).read_text()
            for name in {c["workflow"] for e in entries for c in e["coverage"]}
        }
        if workflows is None
        else workflows
    )
    for entry in entries:
        module, expected = entry["module"], entry["nodes"]
        assert expected and len(expected) == len(set(expected))
        assert all(node.split("::")[0] == module for node in expected)
        covered = []
        for coverage in entry["coverage"]:
            source = workflows[coverage["workflow"]]
            assert "pull_request:" in source and "uses: actions/checkout@" in source
            assert "          ref:" not in source
            blocks = re.split(r"(?=^      - name: )", source, flags=re.MULTILINE)
            matches = [
                b
                for b in blocks
                if b.startswith("      - name: " + coverage["step"] + "\n")
            ]
            assert len(matches) == 1
            block = matches[0]
            assert hashlib.sha256(block.encode()).hexdigest() == coverage["sha256"]
            assert module in block and "--junitxml=" in block
            assert not any(
                value in block
                for value in ("--ignore", "--deselect", " -k ", "continue-on-error")
            )
            group = coverage["group"]
            if group is not None:
                assert (
                    "python script/sql_candidate_proof.py --group " + group + " "
                    in block
                )
                assert "--collection " in block and "--junit " in block
                covered.extend(
                    n for n in inventory[group] if n.split("::")[0] == module
                )
            else:
                assert (
                    module == "tests/integration/test_workflow_current_head_baseline.py"
                )
                assert "assert len(cases)==1" in block
                assert (
                    "assert {case.get('name') for case in cases}=={'test_actual_current032g_workflow_completes_with_readonly_graph_checks'}"
                    in block
                )
                assert (
                    "assert not any(case.find(tag) is not None for case in cases for tag in ('failure','error','skipped'))"
                    in block
                )
                covered.append(
                    module
                    + "::test_actual_current032g_workflow_completes_with_readonly_graph_checks"
                )
        assert Counter(covered) == Counter(expected)
    return modules


def verify_collection(source):
    data = load()
    expected = [node for entry in data["experiments"] for node in entry["nodes"]]
    actual = [
        line
        for line in source.splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    assert Counter(actual) == Counter(expected), (
        "Experimental collection differs from dedicated coverage"
    )


def verify_v1_additions():
    data = load()
    source = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    assert source.count(NEW_COMMENT) == 1
    restored = source.replace(NEW_COMMENT, OLD_COMMENT, 1)
    for entry in data["experiments"]:
        line = "          --ignore=" + entry["module"] + "\n"
        assert restored.count(line) == 1
        restored = restored.replace(line, "", 1)
    assert hashlib.sha256(restored.encode()).hexdigest() == data["v1_original_sha256"]


def verify_sql_additions():
    data = load()
    source = (ROOT / ".github/workflows/mineagent-sql-candidates.yml").read_text()
    step = data["sql_partition_step"]
    assert source.count(step) == 1
    restored = source.replace(step, "", 1)
    for path in data["sql_added_paths"]:
        line = '      - "' + path + '"\n'
        assert restored.count(line) == 1
        restored = restored.replace(line, "", 1)
    assert hashlib.sha256(restored.encode()).hexdigest() == data["sql_original_sha256"]
