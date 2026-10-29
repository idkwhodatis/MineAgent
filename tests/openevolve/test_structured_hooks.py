"""Exact import seam construction; not installed wheel or actual upstream run."""

import json
from pathlib import Path

import pytest

from runtimes.openevolve_structured.apply import TARGETS, expected_inventory, rewrite


def test_full_post_patch_inventory_changes_only_four_hook_modules():
    manifest = json.loads(
        Path("runtimes/openevolve_structured/hook-manifest.json").read_bytes()
    )
    before = expected_inventory()
    after = manifest["installed_source_inventory"]
    assert set(before) == set(after)
    assert {name for name in before if before[name] != after[name]} == set(TARGETS)
    assert all(
        before[name] == item["before_sha256"] and after[name] == item["after_sha256"]
        for name, item in manifest["files"].items()
    )


@pytest.mark.parametrize("target", list(TARGETS))
def test_hook_rewrite_requires_exact_import_count_and_new_namespace(target):
    raw = (
        "from packages.openevolve_adapter import observer\n" * TARGETS[target]
    ).encode()
    result = rewrite(target, raw)
    assert b"packages.openevolve_adapter" not in result
    assert result.count(b"from packages.openevolve_structured") == TARGETS[target]
    with pytest.raises(ValueError):
        rewrite(target, raw + b"from packages.openevolve_adapter import observer\n")
    with pytest.raises(ValueError):
        rewrite(target, result)
