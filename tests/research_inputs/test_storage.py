import os
import time
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app.research_input_storage import (
    input_object_inventory,
    read_input_object,
    verify_payload,
)
from backend.app.services import DomainError

OWNER = UUID(int=123)


def store(tmp_path, content=b"{broken json"):
    sha = sha256(content).hexdigest()
    key = f"factors/{OWNER}/input-objects/{sha}.raw"
    path = tmp_path / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path, SimpleNamespace(
        owner_id=OWNER,
        kind="raw_external",
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
    )


def test_malformed_raw_remains_retained(tmp_path):
    path, row = store(tmp_path)
    assert read_input_object(tmp_path, row, OWNER) == path.read_bytes()


def test_empty_raw_is_evidence(tmp_path):
    _, row = store(tmp_path, b"")
    assert read_input_object(tmp_path, row, OWNER) == b""


@pytest.mark.parametrize(
    "change", ["symlink", "hardlink", "hash", "size", "owner", "path"]
)
def test_invalid_physical_binding(tmp_path, change):
    path, row = store(tmp_path)
    if change == "symlink":
        path.rename(path.with_suffix(".other"))
        path.symlink_to(path.with_suffix(".other"))
    elif change == "hardlink":
        os.link(path, path.with_suffix(".other"))
    elif change == "hash":
        path.write_bytes(b"changed")
    elif change == "size":
        row.size_bytes += 1
    elif change == "owner":
        row.owner_id = UUID(int=124)
    else:
        row.storage_key = row.storage_key.replace("input-objects", "elsewhere")
    with pytest.raises((ValueError, OSError, DomainError)):
        read_input_object(tmp_path, row, OWNER)


def test_inventory_counts_unreferenced_raw_and_refuses_unknown(tmp_path):
    path, _ = store(tmp_path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert input_object_inventory(
            fd, deadline=time.monotonic() + 10, maximum_files=1
        ) == (12, 1)
        (path.parent / "temporary").write_bytes(b"1")
        with pytest.raises(ValueError):
            input_object_inventory(fd, deadline=time.monotonic() + 10, maximum_files=2)
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "kind,content",
    [("raw_external", b"a" * 65537), ("normalized_external", b"{}"), ("other", b"a")],
)
def test_closed_kind_and_byte_limits(kind, content):
    with pytest.raises(ValueError):
        verify_payload(kind, content)


def test_existing_schema_never_activates_new_namespace():
    from backend.app.research_input_storage import require_input_capability

    session = SimpleNamespace(
        scalars=lambda _: SimpleNamespace(
            all=lambda: ["025_structured_factor_strategy"]
        )
    )
    with pytest.raises(DomainError, match="unavailable"):
        require_input_capability(session)


def test_new_publication_does_not_silently_adopt_matching_orphan(tmp_path):
    from backend.app.research_input_storage import reject_unregistered_target

    path, row = store(tmp_path)
    with pytest.raises(DomainError):
        reject_unregistered_target(tmp_path, row.storage_key)
    assert path.read_bytes() == b"{broken json"
    reject_unregistered_target(tmp_path, row.storage_key + ".missing")
