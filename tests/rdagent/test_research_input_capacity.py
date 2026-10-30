import hashlib
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.factor_services import physical_inventory
from backend.app.research_input_capacity import input_inventory_enabled
from backend.app.services import DomainError


def session(revisions):
    return SimpleNamespace(scalars=lambda *a: SimpleNamespace(all=lambda: revisions))


@pytest.mark.parametrize(
    "revision,expected",
    [
        ("027c_seeded_controller_v4", False),
        ("028_oe_structured_search", False),
        ("029_research_workflow", True),
        ("030_declared_upload_basket", True),
        ("031_workflow_structured_stage", True),
        ("032_normal_oe_note_pins", True),
    ],
)
def test_exact_capability(revision, expected):
    assert input_inventory_enabled(session([revision])) is expected


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["future"],
        ["033_safe_workflow_branches"],
        ["029_research_workflow", "032_normal_oe_note_pins"],
    ],
)
def test_unknown_or_multiple_revision_rejected(revisions):
    with pytest.raises(DomainError):
        input_inventory_enabled(session(revisions))


@pytest.mark.parametrize(
    "revision,value",
    [
        ("028_oe_structured_search", "mineagent.research-inputs.v1"),
        ("032_normal_oe_note_pins", None),
        ("032_normal_oe_note_pins", "future"),
    ],
)
def test_capability_disagreement_rejected(monkeypatch, revision, value):
    from backend.app import schema_capabilities

    monkeypatch.setattr(
        schema_capabilities,
        "capabilities_for",
        lambda _: SimpleNamespace(research_input_schema=value),
    )
    with pytest.raises(DomainError):
        input_inventory_enabled(session([revision]))


def raw(root, owner, content):
    directory = root / "factors" / str(owner) / "input-objects"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(content).hexdigest() + ".raw")
    path.write_bytes(content)
    return path


def inventory(root, owner, *, enabled=True, maximum=100, deadline=None):
    fd = os.open(root / "factors", os.O_RDONLY | os.O_DIRECTORY)
    try:
        return physical_inventory(
            fd,
            owner,
            time.monotonic() + 5 if deadline is None else deadline,
            maximum,
            include_input_inventory=enabled,
            include_dataset_inventory=True,
            storage_root=root,
        )
    finally:
        os.close(fd)


def test_mixed_orphan_bytes_are_charged_to_owner_and_installation(tmp_path):
    owner, other = uuid4(), uuid4()
    raw(tmp_path, owner, b"x" * 12)
    raw(tmp_path, other, b"y" * 17)
    objects = tmp_path / "factors" / str(owner) / "objects"
    objects.mkdir()
    (objects / ("a" * 64 + ".json")).write_bytes(b"{}")
    upload = tmp_path / "datasets" / str(owner) / str(uuid4())
    upload.mkdir(parents=True)
    (upload / "orphan.csv").write_bytes(b"z" * 7)
    assert inventory(tmp_path, owner) == (38, 21, 11)
    assert inventory(tmp_path, other) == (38, 17, 11)
    with pytest.raises(DomainError):
        inventory(tmp_path, owner, enabled=False)
    with pytest.raises(DomainError):
        inventory(tmp_path, owner, maximum=10)
    with pytest.raises(DomainError):
        inventory(tmp_path, owner, deadline=time.monotonic() - 1)


@pytest.mark.parametrize(
    "kind",
    [
        "hash",
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "temporary",
        "oversize",
        "noncanonical-owner",
    ],
)
def test_unsafe_input_namespace_rejected(tmp_path, kind):
    owner = uuid4()
    path = raw(tmp_path, owner, b"known")
    if kind == "hash":
        path.write_bytes(b"other")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    elif kind == "hardlink":
        os.link(path, tmp_path / "second-link")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "temporary":
        path.rename(path.with_name("." + path.name + ".tmp"))
    elif kind == "oversize":
        path.unlink()
        raw(tmp_path, owner, b"x" * 65537)
    else:
        path.parent.parent.rename(path.parent.parent.with_name(str(owner).upper()))
    with pytest.raises(DomainError):
        inventory(tmp_path, owner)


@pytest.mark.parametrize(
    "revision",
    [
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
    ],
)
@pytest.mark.parametrize("limit", ["owner", "installation"])
@pytest.mark.parametrize("explicit", [False, True])
def test_publication_charges_input_orphans(
    tmp_path, monkeypatch, limit, explicit, revision
):
    from backend.app import (
        execution_snapshot_capacity,
        factor_native_services,
        factor_services,
        rdagent_source_capacity,
    )
    from runtimes.factor_parser import supervisor

    owner = uuid4()
    (tmp_path / "factors").mkdir()
    # Two independent writes, no committed row or manifest needed to retain charge.
    for content in (b"x" * 60, b"y" * 60):
        raw(tmp_path, owner, content)
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
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision]),
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


@pytest.mark.parametrize(
    "revision",
    [
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
    ],
)
@pytest.mark.parametrize("publisher", ["source", "native", "snapshot"])
@pytest.mark.parametrize("limit", ["owner", "installation"])
def test_internal_publishers_charge_input_orphans(
    tmp_path, monkeypatch, revision, publisher, limit
):
    from backend.app import (
        execution_snapshot_capacity as snapshots,
    )
    from backend.app import (
        execution_snapshot_services as snapshot_writer,
    )
    from backend.app import (
        factor_native_services as native_writer,
    )
    from backend.app import (
        rdagent_source_capacity as sources,
    )
    from backend.app import (
        rdagent_source_publication as source_writer,
    )
    from runtimes.factor_parser import supervisor

    owner, other = uuid4(), uuid4()
    (tmp_path / "factors").mkdir()
    for who, size in ((owner, 120), (other, 17)):
        raw(tmp_path, who, b"x" * size)
    monkeypatch.setattr(supervisor, "assert_quiescent", lambda *a: None)
    monkeypatch.setattr(native_writer, "native_capability", lambda *a: True)
    monkeypatch.setattr(native_writer, "verify_native_journals", lambda *a: None)
    monkeypatch.setattr(native_writer, "pending_native_usage", lambda *a: (0, 0, 0))
    monkeypatch.setattr(sources, "pending_source_usage", lambda *a: (0, 0, 0))
    monkeypatch.setattr(
        snapshots, "pending_snapshot_usage_for_schema", lambda *a: (0, 0, 0)
    )
    monkeypatch.setattr(snapshot_writer, "pending_snapshot_usage", lambda *a: (0, 0, 0))
    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision])
    )
    fence = SimpleNamespace(owner_id=owner)
    settings = SimpleNamespace(
        storage_root=tmp_path,
        factor_max_files=100,
        factor_owner_max_bytes=100 if limit == "owner" else 1000,
        factor_installation_max_bytes=100 if limit == "installation" else 1000,
    )
    fd = os.open(tmp_path / "factors", os.O_RDONLY | os.O_DIRECTORY)
    try:
        if publisher == "source":
            # This shared scanner returns counts; reserve_source enforces quota.
            assert source_writer._native_aware_inventory(
                session, fd, fence, settings
            ) == (137, 120, 6)
        else:
            writer = native_writer if publisher == "native" else snapshot_writer
            with pytest.raises(DomainError):
                writer._capacity(session, fence, settings, fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("exceeds", [False, True])
def test_publication_rescans_new_input_namespace(tmp_path, monkeypatch, exceeds):
    from backend.app import (
        execution_snapshot_capacity,
        factor_native_services,
        factor_services,
        rdagent_source_capacity,
    )
    from runtimes.factor_parser import supervisor

    owner = uuid4()
    (tmp_path / "factors").mkdir()
    monkeypatch.setattr(factor_services, "try_lock", lambda *a: None)
    monkeypatch.setattr(factor_native_services, "native_capability", lambda *a: False)
    monkeypatch.setattr(
        rdagent_source_capacity, "pending_source_usage_for_schema", lambda *a: (0, 0, 0)
    )
    monkeypatch.setattr(
        execution_snapshot_capacity,
        "pending_snapshot_usage_for_schema",
        lambda *a: (0, 0, 0),
    )
    monkeypatch.setattr(supervisor, "assert_quiescent", lambda *a: None)
    db = session(["032_normal_oe_note_pins"])
    db.rollback = lambda: None
    db.scalar = lambda *a, **k: owner
    db.is_active = True
    db.connection = lambda: SimpleNamespace(invalidated=False)
    db.execute = lambda *a, **k: None
    db.commit = lambda: None
    settings = SimpleNamespace(
        storage_root=tmp_path,
        factor_library_enabled=True,
        factor_max_files=100,
        factor_owner_max_bytes=100,
        factor_installation_max_bytes=100,
    )

    def publish():
        with factor_services.publication(db, owner, settings, peak_bytes=12):
            raw(tmp_path, owner, b"x" * (101 if exceeds else 12))

    if exceeds:
        with pytest.raises(DomainError):
            publish()
    else:
        publish()
        assert inventory(tmp_path, owner) == (12, 12, 3)


def test_typed_input_directory_replacement_is_rejected(tmp_path, monkeypatch):
    from backend.app import research_input_storage

    owner = uuid4()
    path = raw(tmp_path, owner, b"known")
    original = research_input_storage.input_object_inventory

    def changed(fd, **kwargs):
        result = original(fd, **kwargs)
        directory = path.parent
        directory.rename(directory.with_name("moved"))
        directory.mkdir()
        return result

    monkeypatch.setattr(research_input_storage, "input_object_inventory", changed)
    with pytest.raises(DomainError):
        inventory(tmp_path, owner)
