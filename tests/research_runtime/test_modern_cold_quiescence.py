"""Real offline journal bytes; no Docker or successful-result claim."""

import importlib
import json
import shutil
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.supervisor import assert_quiescent

FACTORIES = {
    "rd": "tests.rdagent.test_rdagent_controller_process",
    "oe": "tests.openevolve.operational_fixtures",
    "analysis": "tests.qlib.test_analysis_journal",
    "native": "tests.openevolve.test_native_evaluator_journal",
    "structured": "tests.structured_factor_strategy.test_process_journal",
}


def persist(root, kind):
    if kind == "native":
        from tests.openevolve.test_native_evaluator_output_reader import fixture

        return fixture(root)[1]
    factory = importlib.import_module(FACTORIES[kind]).records
    values = factory(root) if kind in {"rd", "oe"} else factory()
    admission = values[0].intent.admission
    operation = (
        root / "research-processes" / str(admission.owner_id) / str(admission.id)
    )
    control = operation / "control"
    control.mkdir(parents=True)
    names = (
        ("launch", "identity", "child", "stop")
        if kind in {"rd", "oe"}
        else ("launch", "identity", "stop", "extraction")
    )
    for name, value in zip(names, values, strict=True):
        path = control / (name + ".json")
        path.write_bytes(canonical(value.model_dump(mode="json")))
        path.chmod(0o600)
    if kind not in {"rd", "oe"}:
        (operation / "output").mkdir()
        (operation / "output" / values[-1].basename).write_bytes(b"opaque")
    return operation


@pytest.mark.parametrize("kind", FACTORIES)
def test_complete_stopped_journal_and_relocated_root(tmp_path, kind):
    source = tmp_path / "source"
    source.mkdir()
    persist(source, kind)
    assert_quiescent(source)
    restored = tmp_path / "restored"
    shutil.copytree(source, restored)
    assert source.stat().st_ino != restored.stat().st_ino
    assert_quiescent(restored)


@pytest.mark.parametrize("kind", FACTORIES)
@pytest.mark.parametrize(
    "damage",
    [
        "missing_stop",
        "live",
        "hash",
        "profile",
        "owner",
        "unit",
        "extra_control",
        "extra_directory",
        "symlink",
    ],
)
def test_incomplete_or_forged_inventory_fails_closed(tmp_path, kind, damage):
    operation = persist(tmp_path, kind)
    control = operation / "control"
    path = control / "stop.json"
    if damage == "missing_stop":
        path.unlink()
    elif damage in {"live", "hash", "profile"}:
        if damage == "profile":
            path = control / "launch.json"
        value = json.loads(path.read_bytes())
        if damage == "live":
            value["confirmed_absent"] = False
        elif damage == "hash":
            value["launch_sha256"] = "0" * 64
        else:
            value["intent"]["admission"]["profile"] = "unknown"
        path.write_bytes(canonical(value))
    elif damage == "owner":
        operation.parent.rename(operation.parent.with_name(str(uuid4())))
    elif damage == "unit":
        operation.rename(operation.with_name(str(uuid4())))
    elif damage == "extra_control":
        (control / "unknown.json").write_bytes(b"{}")
    elif damage == "extra_directory":
        (operation / "input").mkdir()
    else:
        outside = tmp_path / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
    with pytest.raises((ProcessError, OSError, ValueError)):
        assert_quiescent(tmp_path)


@pytest.mark.parametrize("kind", ["rd", "oe"])
def test_stopped_bootstrap_without_child_is_not_invented_peer(tmp_path, kind):
    operation = persist(tmp_path, kind)
    path = operation / "control" / "stop.json"
    value = json.loads(path.read_bytes())
    value.update(child_sha256=None, child_kernel_identity_absent=None)
    path.write_bytes(canonical(value))
    with pytest.raises(ProcessError):
        assert_quiescent(tmp_path)
    (operation / "control" / "child.json").unlink()
    assert_quiescent(tmp_path)


@pytest.mark.parametrize("kind", FACTORIES)
def test_unknown_schema_does_not_bypass_validation(tmp_path, kind):
    operation = persist(tmp_path, kind)
    path = operation / "control" / "launch.json"
    value = json.loads(path.read_bytes())
    value["schema_version"] = "mineagent.unknown-launch.v1"
    path.write_bytes(canonical(value))
    with pytest.raises(ProcessError):
        assert_quiescent(tmp_path)


@pytest.mark.parametrize("kind", ["analysis", "native", "structured"])
def test_stopped_without_result_is_only_containment(tmp_path, kind):
    operation = persist(tmp_path, kind)
    (operation / "control" / "extraction.json").unlink()
    for path in (operation / "output").iterdir():
        path.unlink()
    assert_quiescent(tmp_path)


@pytest.mark.parametrize("kind", ["analysis", "native", "structured"])
@pytest.mark.parametrize("damage", ["hash", "missing_output", "extra_output"])
def test_extraction_and_output_inventory_remain_bound(tmp_path, kind, damage):
    operation = persist(tmp_path, kind)
    if damage == "hash":
        path = operation / "control" / "extraction.json"
        value = json.loads(path.read_bytes())
        value["identity_sha256"] = "0" * 64
        path.write_bytes(canonical(value))
    elif damage == "missing_output":
        for path in (operation / "output").iterdir():
            path.unlink()
    else:
        (operation / "output" / "unknown").write_bytes(b"unknown")
    with pytest.raises(ProcessError):
        assert_quiescent(tmp_path)


@pytest.mark.parametrize(
    "kind,component",
    [
        (kind, component)
        for kind in FACTORIES
        for component in ("operation", "control", "output")
        if component != "output" or kind not in {"rd", "oe"}
    ],
)
def test_directory_links_never_followed(tmp_path, kind, component):
    operation = persist(tmp_path, kind)
    path = operation if component == "operation" else operation / component
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ProcessError, OSError)):
        assert_quiescent(tmp_path)


def test_native_production_cleanup_retains_bound_manifest(tmp_path):
    from runtimes.research_supervisor.openevolve_native_supervisor import (
        NativeSupervisor,
        _write_bytes,
        staged_name,
    )
    from tests.openevolve.test_native_evaluator_contracts import manifest
    from tests.openevolve.test_native_evaluator_output_reader import fixture

    admission, operation, _ = fixture(tmp_path)
    supervisor = NativeSupervisor(admission.image_id, "sha256:" + "f" * 64)
    launch = supervisor._launch(operation)
    inputs = operation / "inputs"
    inputs.mkdir(mode=0o700)
    _, files = manifest()
    for name, raw in files.items():
        _write_bytes(inputs / staged_name("/input/" + name), raw, 0o444)
    supervisor._remove_inputs(operation, launch)
    assert {p.name for p in operation.iterdir()} == {
        "control",
        "output",
        "manifest.json",
    }
    assert_quiescent(tmp_path)


@pytest.mark.parametrize(
    "damage",
    ["missing", "changed", "noncanonical", "symlink", "hardlink", "mode", "oversize"],
)
def test_native_manifest_is_required_and_bound(tmp_path, damage):
    operation = persist(tmp_path, "native")
    path = operation / "manifest.json"
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        value = json.loads(path.read_bytes())
        value["execution_plan_sha256"] = "0" * 64
        path.write_bytes(canonical(value))
    elif damage == "noncanonical":
        path.write_bytes(path.read_bytes() + b"\n")
    elif damage == "symlink":
        outside = tmp_path / "outside-manifest.json"
        path.rename(outside)
        path.symlink_to(outside)
    elif damage == "hardlink":
        import os

        os.link(path, tmp_path / "manifest-link")
    elif damage == "mode":
        path.chmod(0o644)
    else:
        path.write_bytes(b" " * 65537)
    with pytest.raises((ProcessError, OSError, ValueError)):
        assert_quiescent(tmp_path)
