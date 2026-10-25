"""Closed native inventory/schema validation independent of upstream import."""

import json
from uuid import uuid4

import pytest

from packages.openevolve_adapter.checkpoint_native import (
    state_sha256,
    validate_directory,
)


@pytest.fixture
def native(tmp_path):
    pid = str(uuid4())
    program = {
        "id": pid,
        "code": "probe = 0\n",
        "changes_description": "",
        "language": "python",
        "parent_id": None,
        "generation": 0,
        "timestamp": 1.0,
        "iteration_found": 0,
        "metrics": {"combined_score": -5.0},
        "complexity": 0.0,
        "diversity": 0.0,
        "metadata": {"island": 0},
        "prompts": None,
        "artifacts_json": None,
        "artifact_dir": None,
        "embedding": None,
    }
    metadata = {
        "island_feature_maps": [{"0": pid}],
        "islands": [[pid]],
        "archive": [pid],
        "best_program_id": pid,
        "island_best_programs": [pid],
        "last_iteration": 0,
        "current_island": 0,
        "island_generations": [0],
        "last_migration_generation": 0,
        "feature_stats": {},
    }
    (tmp_path / "programs").mkdir()
    (tmp_path / "programs" / f"{pid}.json").write_text(json.dumps(program))
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    (tmp_path / "best_program.py").write_text(program["code"])
    info = {
        "id": pid,
        "generation": 0,
        "iteration": 0,
        "current_iteration": 0,
        "metrics": program["metrics"],
        "language": "python",
        "timestamp": 1.0,
        "saved_at": 2.0,
    }
    (tmp_path / "best_program_info.json").write_text(json.dumps(info))
    return tmp_path


def test_native_valid_inventory(native):
    state = validate_directory(native)
    assert state["metadata"]["last_iteration"] == 0
    assert len(state_sha256(state)) == 64


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "disk",
        "prompts",
        "future",
        "bad_code",
        "dangling",
        "duplicate",
        "missing",
        "extra",
        "symlink",
        "best",
    ],
)
def test_native_rejects_loader_leniency(native, change):
    file = next((native / "programs").iterdir())
    value = json.loads(file.read_text())
    if change in {"unknown", "disk", "prompts", "future", "bad_code"}:
        key, data = {
            "unknown": ("extra", 1),
            "disk": ("artifact_dir", "/tmp/not-owned"),
            "prompts": ("prompts", {}),
            "future": ("iteration_found", 1),
            "bad_code": ("code", "x\r\n"),
        }[change]
        value[key] = data
        file.write_text(json.dumps(value))
    elif change == "dangling":
        path = native / "metadata.json"
        value = json.loads(path.read_text())
        value["archive"] = [str(uuid4())]
        path.write_text(json.dumps(value))
    elif change == "duplicate":
        file.write_text('{"id":"x","id":"y"}')
    elif change == "missing":
        file.unlink()
    elif change == "extra":
        (native / "extra").write_text("x")
    elif change == "symlink":
        file.unlink()
        file.symlink_to(native / "metadata.json")
    elif change == "best":
        (native / "best_program.py").write_text("different\n")
    with pytest.raises(ValueError):
        validate_directory(native)


def test_native_null_description_matches_real_upstream_child(native):
    file = next((native / "programs").iterdir())
    value = json.loads(file.read_text())
    value["changes_description"] = None
    file.write_text(json.dumps(value))
    assert (
        validate_directory(native)["programs"][value["id"]]["changes_description"]
        is None
    )
