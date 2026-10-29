import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import factor_services
from backend.app.declared_upload_capacity import dataset_physical_usage
from backend.app.services import DomainError


def test_all_original_and_orphan_dataset_bytes_are_charged(tmp_path):
    owner, other = uuid4(), uuid4()
    for who, size in [(owner, 7), (owner, 11), (other, 13)]:
        path = tmp_path / "datasets" / str(who) / str(uuid4()) / "sources" / "0"
        path.mkdir(parents=True)
        (path / "uncommitted.csv").write_bytes(b"x" * size)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        total, owned, count = dataset_physical_usage(
            fd, owner, time.monotonic() + 5, 100
        )
        assert (total, owned) == (31, 18)
        assert count > 3
        with pytest.raises(ValueError, match="bound"):
            dataset_physical_usage(fd, owner, time.monotonic() + 5, 2)
    finally:
        os.close(fd)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_orphan_files_fail_closed(tmp_path, kind):
    owner = uuid4()
    path = tmp_path / "datasets" / str(owner) / str(uuid4())
    path.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_bytes(b"x")
    if kind == "symlink":
        (path / "bad").symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path / "bad")
    else:
        os.mkfifo(path / "bad")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError):
            dataset_physical_usage(fd, owner, time.monotonic() + 5, 100)
    finally:
        os.close(fd)


@pytest.mark.parametrize("limit", ["owner", "installation"])
@pytest.mark.parametrize("explicit", [False, True])
def test_all_030_publishers_reject_accumulated_dataset_bytes(
    tmp_path, monkeypatch, limit, explicit
):
    from backend.app import (
        execution_snapshot_capacity,
        factor_native_services,
        rdagent_source_capacity,
    )
    from runtimes.factor_parser import supervisor

    owner = uuid4()
    (tmp_path / "factors").mkdir()
    # Two independent writes, no committed row or manifest needed to retain charge.
    for size in (60, 60):
        path = tmp_path / "datasets" / str(owner) / str(uuid4())
        path.mkdir(parents=True)
        (path / "orphan").write_bytes(b"x" * size)
    monkeypatch.setattr(factor_services, "try_lock", lambda *a: None)
    monkeypatch.setattr(factor_native_services, "native_capability", lambda s: False)
    monkeypatch.setattr(
        rdagent_source_capacity, "pending_source_usage_for_schema", lambda *a: (0, 0, 0)
    )
    monkeypatch.setattr(
        execution_snapshot_capacity,
        "pending_snapshot_usage_for_schema",
        lambda *a: (0, 0, 0),
    )
    monkeypatch.setattr(supervisor, "assert_quiescent", lambda root: None)
    session = SimpleNamespace(
        rollback=lambda: None,
        scalar=lambda *a, **k: owner,
        scalars=lambda *a: SimpleNamespace(all=lambda: ["030_declared_upload_basket"]),
    )
    settings = SimpleNamespace(
        storage_root=tmp_path,
        factor_library_enabled=True,
        factor_max_files=100,
        factor_owner_max_bytes=100 if limit == "owner" else 1000,
        factor_installation_max_bytes=100 if limit == "installation" else 1000,
    )
    with (
        pytest.raises(DomainError, match="physical quota exceeded"),
        factor_services.publication(
            session, owner, settings, peak_bytes=1, include_dataset_inventory=explicit
        ),
    ):
        raise AssertionError("Over-budget writer admitted")
