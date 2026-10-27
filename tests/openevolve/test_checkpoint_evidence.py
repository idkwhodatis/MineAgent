"""Registered envelope/native bytes checked without filesystem access."""

import base64
import hashlib
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from packages.openevolve_adapter.checkpoint_evidence import validate_checkpoint_evidence
from packages.openevolve_adapter.checkpoint_native import (
    _normalize,
    canonical,
    state_sha256,
)
from packages.openevolve_adapter.host_contracts import (
    CheckpointFileEnvelope,
    CheckpointFileRef,
    CheckpointInventory,
    EvidenceRef,
)


def fixture():
    pid = str(uuid4())
    program = {
        "id": pid,
        "code": "probe = 0\n",
        "changes_description": None,
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
    native = {
        "metadata.json": canonical(metadata),
        f"programs/{pid}.json": canonical(program),
        "best_program.py": program["code"].encode(),
        "best_program_info.json": canonical(info),
    }
    return native, state_sha256(_normalize(metadata, {pid: program}))


def wrap(native):
    files, refs = {}, []
    for name, data in sorted(native.items()):
        envelope = CheckpointFileEnvelope(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            data_base64=base64.b64encode(data).decode(),
        )
        raw = canonical(envelope.model_dump(mode="json"))
        files[name] = raw
        refs.append(
            CheckpointFileRef(
                name=name,
                file_sha256=envelope.sha256,
                size_bytes=len(data),
                object=EvidenceRef(id=uuid4(), sha256=hashlib.sha256(raw).hexdigest()),
            )
        )
    return CheckpointInventory(files=tuple(refs)), files


def test_pure_valid_checkpoint():
    native, expected = fixture()
    inventory, files = wrap(native)
    with patch(
        "builtins.open", side_effect=AssertionError("Filesystem access forbidden")
    ):
        state = validate_checkpoint_evidence(inventory, files, expected)
    assert state["metadata"]["last_iteration"] == 0


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "object_hash",
        "native_hash",
        "binding",
        "base64",
        "duplicate_keys",
        "state",
        "duplicate_object",
    ],
)
def test_envelope_integrity_and_exact_inventory(change):
    native, expected = fixture()
    inventory, files = wrap(native)
    name = "metadata.json"
    if change == "missing":
        files.pop(name)
    elif change == "extra":
        files["unexpected"] = b"{}"
    elif change == "object_hash":
        files[name] += b" "
    elif change == "state":
        expected = "a" * 64
    elif change == "duplicate_object":
        refs = list(inventory.files)
        refs[1] = refs[1].model_copy(update={"object": refs[0].object})
        inventory = inventory.model_copy(update={"files": tuple(refs)})
    else:
        envelope = json.loads(files[name])
        if change == "native_hash":
            envelope["sha256"] = "a" * 64
        elif change == "binding":
            envelope["name"] = "best_program.py"
        elif change == "base64":
            envelope["data_base64"] = "!!!!"
        raw = canonical(envelope)
        if change == "duplicate_keys":
            raw = raw[:-1] + b',"name":"metadata.json"}'
        files[name] = raw
        refs = [
            r.model_copy(
                update={
                    "object": EvidenceRef(
                        id=r.object.id, sha256=hashlib.sha256(raw).hexdigest()
                    )
                }
            )
            if r.name == name
            else r
            for r in inventory.files
        ]
        inventory = inventory.model_copy(update={"files": tuple(refs)})
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(inventory, files, expected)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_program",
        "wrong_program_id",
        "best_source",
        "best_info",
        "saved_nan",
        "saved_inf",
        "saved_bool",
        "unknown_info",
        "duplicate_native_key",
        "native_nonfinite",
    ],
)
def test_native_schema_and_projection(change):
    native, expected = fixture()
    program_name = next(n for n in native if n.startswith("programs/"))
    if change in {"unknown_program", "wrong_program_id"}:
        program = json.loads(native[program_name])
        program["unknown" if change == "unknown_program" else "id"] = str(uuid4())
        native[program_name] = canonical(program)
    elif change == "best_source":
        native["best_program.py"] = b"different\n"
    elif change == "duplicate_native_key":
        native["metadata.json"] = b'{"last_iteration":0,"last_iteration":1}'
    elif change == "native_nonfinite":
        native["metadata.json"] = native["metadata.json"].replace(
            b'"last_iteration":0', b'"last_iteration":1e999'
        )
    else:
        info = json.loads(native["best_program_info.json"])
        if change == "best_info":
            info["current_iteration"] = True
        elif change == "unknown_info":
            info["unknown"] = 0
        else:
            info["saved_at"] = {
                "saved_nan": float("nan"),
                "saved_inf": float("inf"),
                "saved_bool": True,
            }[change]
        native["best_program_info.json"] = json.dumps(info).encode()
    inventory, files = wrap(native)
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(inventory, files, expected)


def test_typed_inventory_cannot_bypass_total_bound():
    native, expected = fixture()
    inventory, files = wrap(native)
    refs = tuple(
        inventory.files[0].model_copy(
            update={"name": f"programs/{uuid4()}.json", "size_bytes": 524288}
        )
        for _ in range(17)
    )
    bad = inventory.model_copy(
        update={"files": tuple(sorted((*inventory.files, *refs), key=lambda r: r.name))}
    )
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(bad, files, expected)


@pytest.mark.parametrize("raw_value", [b"1e999", b"1" + b"0" * 1000])
def test_overflowing_saved_at_is_rejected(raw_value):
    native, expected = fixture()
    native["best_program_info.json"] = native["best_program_info.json"].replace(
        b'"saved_at":2.0', b'"saved_at":' + raw_value
    )
    inventory, files = wrap(native)
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(inventory, files, expected)


def test_duplicate_program_uuid_inventory_is_rejected():
    native, expected = fixture()
    inventory, files = wrap(native)
    ref = next(r for r in inventory.files if r.name.startswith("programs/"))
    inventory = inventory.model_copy(update={"files": (*inventory.files, ref)})
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(inventory, files, expected)


def test_unknown_inventory_path_cannot_bypass_typed_model():
    native, expected = fixture()
    inventory, files = wrap(native)
    refs = list(inventory.files)
    refs[0] = refs[0].model_copy(update={"name": "../escape"})
    inventory = inventory.model_copy(update={"files": tuple(refs)})
    with pytest.raises(ValueError):
        validate_checkpoint_evidence(inventory, files, expected)
