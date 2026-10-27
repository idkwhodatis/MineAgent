"""Build inventory/workflow checks, never actual image acceptance."""

from pathlib import Path

from runtimes.openevolve_evaluator.recipe import (
    FILES,
    wrapper_inventory,
    wrapper_inventory_sha256,
)


def test_exact_wrapper_recipe_covers_only_explicit_copy_sources():
    value = wrapper_inventory()
    assert {row["path"] for row in value["files"]} == set(FILES)
    assert len(wrapper_inventory_sha256()) == 64
    recipe = Path("runtimes/openevolve_evaluator/Dockerfile").read_text()
    ignore = set(
        Path("runtimes/openevolve_evaluator/Dockerfile.dockerignore")
        .read_text()
        .splitlines()
    )
    for line in recipe.splitlines():
        if line.startswith("COPY "):
            for name in line.split()[1:-1]:
                assert name in FILES and "!" + name in ignore
    assert "COPY src/" not in recipe


def test_actual_gate_has_exact_two_no_skip_units_and_no_result_upload():
    text = Path(".github/workflows/mineagent-openevolve-native.yml").read_text()
    assert "assert len(cases)==2" in text
    assert "('failure','error','skipped')" in text
    assert "test_actual_native_owner_loss_original_deadline_without_result" in text
    assert "docker build --network none" in text
    assert "contents: read" in text and "persist-credentials: false" in text
    assert "path: var/oe-native-proof/" in text
    assert "docker push" not in text


def test_actual_fixture_stages_maintained_native_source_without_import(tmp_path):
    from uuid import uuid4

    from tests.integration.openevolve_native_fixture import stage_maintained_native

    plan, manifest, files = stage_maintained_native(
        tmp_path, evaluation_id=uuid4(), evaluator_image="sha256:" + "a" * 64
    )
    assert (
        files["/input/strategy.py"]
        == Path("seed_strategies/research_cross.py").read_bytes()
    )
    from packages.research_runtime.broker_contracts import digest

    assert manifest.execution_plan_sha256 == digest(plan)
    assert manifest.native_artifacts_sha256 != manifest.sha256
    assert "/input/stage-report.json" in files
    assert not any("label" in path for path in files)
