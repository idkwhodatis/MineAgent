import hashlib
import os
from uuid import uuid4

import pytest

from backend.app.core.atomic_storage import publish_bytes
from backend.app.research_object_services import (
    _read_exact,
    object_storage_guard,
    physical_inventory,
)
from backend.app.services import DomainError
from packages.research_runtime.object_identity import identity_for_content


def provision(tmp_path):
    (tmp_path / "research-objects").mkdir()
    return tmp_path


def test_real_storage_counts_equal_bytes_under_separate_kinds(tmp_path):
    root = provision(tmp_path)
    owner = uuid4()
    identities = [
        identity_for_content(owner, kind, b"{}")
        for kind in ("model_request", "model_response")
    ]
    with object_storage_guard(root):
        for identity in identities:
            publish_bytes(
                root / identity.storage_key,
                b"{}",
                sha256=identity.sha256,
                durable_root=root,
            )
    inventory = physical_inventory(root)
    assert len(inventory) == 2
    assert sum(item.size_bytes for item in inventory) == 4
    assert {item.storage_key for item in inventory} == {
        i.storage_key for i in identities
    }


def test_exact_republication_reuses_verified_file_and_rejects_corruption(tmp_path):
    root = provision(tmp_path)
    identity = identity_for_content(uuid4(), "model_request", b"{}")
    path = root / identity.storage_key
    for _ in range(2):
        publish_bytes(path, b"{}", sha256=identity.sha256, durable_root=root)
    assert len(physical_inventory(root)) == 1
    assert _read_exact(root, identity.storage_key, identity.sha256, 2) == b"{}"
    path.chmod(0o600)
    path.write_bytes(b"[]")
    with pytest.raises(ValueError):
        publish_bytes(path, b"{}", sha256=identity.sha256, durable_root=root)
    with pytest.raises(DomainError):
        _read_exact(root, identity.storage_key, identity.sha256, 2)


def test_orphan_temporary_bytes_are_charged_not_silently_removed(tmp_path):
    root = provision(tmp_path)
    identity = identity_for_content(uuid4(), "model_response", b'{"x":1}')
    target = root / identity.storage_key
    target.parent.mkdir(parents=True)
    temporary = target.with_name(f".{target.name}.{'b' * 32}.tmp")
    temporary.write_bytes(b'{"x":')
    result = physical_inventory(root)
    assert len(result) == 1 and result[0].temporary and result[0].size_bytes == 5
    assert temporary.read_bytes() == b'{"x":'


@pytest.mark.parametrize(
    "unsafe", ["symlink", "hardlink", "unknown_file", "nested_directory"]
)
def test_inventory_rejects_unsafe_or_unknown_physical_entries(tmp_path, unsafe):
    root = provision(tmp_path)
    identity = identity_for_content(uuid4(), "process_receipt", b"{}")
    path = root / identity.storage_key
    path.parent.mkdir(parents=True)
    if unsafe == "symlink":
        path.symlink_to(tmp_path / "outside")
    elif unsafe == "hardlink":
        other = tmp_path / "other"
        other.write_bytes(b"{}")
        os.link(other, path)
    elif unsafe == "unknown_file":
        path.with_name("arbitrary.bin").write_bytes(b"x")
    else:
        (path.parent / "extra").mkdir()
    with pytest.raises((DomainError, OSError)):
        physical_inventory(root)


def test_root_must_be_provisioned_and_fence_remains_independent(tmp_path):
    with pytest.raises(FileNotFoundError), object_storage_guard(tmp_path):
        pass
    provision(tmp_path)
    with object_storage_guard(tmp_path):
        with pytest.raises(DomainError), object_storage_guard(tmp_path):
            pass
        # Cold storage maintenance remains excluded even without any DB session.
        from backend.app.core.maintenance import MaintenanceBusy
        from backend.app.core.storage_barrier import storage_guard

        with pytest.raises(MaintenanceBusy), storage_guard(tmp_path, exclusive=True):
            pass
    with object_storage_guard(tmp_path):
        assert physical_inventory(tmp_path) == ()


def test_physical_inventory_has_bounded_file_count(tmp_path):
    root = provision(tmp_path)
    owner = uuid4()
    for value in (b"{}", b'{"x":1}'):
        identity = identity_for_content(owner, "model_request", value)
        publish_bytes(
            root / identity.storage_key,
            value,
            sha256=hashlib.sha256(value).hexdigest(),
            durable_root=root,
        )
    with pytest.raises(DomainError):
        physical_inventory(root, max_files=1)
