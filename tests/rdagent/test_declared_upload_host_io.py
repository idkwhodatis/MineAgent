"""Bounded nofollow owned-store readers; no process, network or execution grant."""

import hashlib
import os
from uuid import UUID

import pytest

from backend.app.declared_upload_io import (
    artifact_inventory,
    read_artifact,
    safe_artifact_name,
)
from backend.app.declared_upload_schemas import DeclaredUploadBasketCreate


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "/x",
        "a/../b",
        "a//b",
        "a\\b",
        "a/./b",
        "manifest.json/../x",
        "a/" * 7 + "b",
    ],
)
def test_source_path_escape_rejected(name):
    with pytest.raises(ValueError):
        safe_artifact_name(name)


@pytest.mark.parametrize("change", ["hash", "symlink", "hardlink", "oversize"])
def test_stable_exact_regular_source_required(tmp_path, change):
    key = f"datasets/{UUID(int=1)}/{UUID(int=2)}"
    root = tmp_path / key
    root.mkdir(parents=True)
    path = root / "source.csv"
    path.write_bytes(b"a,b\n1,2\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if change == "hash":
        sha = "0" * 64
    if change == "symlink":
        path.unlink()
        path.symlink_to(root / "missing")
    if change == "hardlink":
        os.link(path, root / "other.csv")
    with pytest.raises((ValueError, OSError)):
        read_artifact(
            tmp_path, key, "source.csv", sha, 1 if change == "oversize" else 100
        )


@pytest.mark.parametrize(
    "value",
    [
        {"artifact_files": {"../x": "a" * 64}},
        {"artifact_files": {"manifest.json": "a" * 64}},
        {"files": {"x": "a" * 64}, "artifact_files": {"x": "b" * 64}},
        {"artifact_files": {"x": False}},
    ],
)
def test_inventory_binding_rejected(value):
    with pytest.raises(ValueError):
        artifact_inventory(value)


@pytest.mark.parametrize("ack", [False, 1, "true", None])
def test_user_acknowledgement_never_coerced(ack):
    with pytest.raises(ValueError):
        DeclaredUploadBasketCreate(
            source_dataset_ids=(UUID(int=1), UUID(int=2)),
            acknowledge_unverified=ack,
            idempotency_key="key",
        )
