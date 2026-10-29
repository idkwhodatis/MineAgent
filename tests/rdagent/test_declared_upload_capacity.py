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
def test_all_030_publishers_reject_accumulated_dataset_bytes(
    tmp_path, monkeypatch, limit, explicit, revision
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
    "revisions, expected",
    [
        (["027c_seeded_controller_v4"], False),
        (["028_oe_structured_search"], False),
        (["030_declared_upload_basket"], True),
        (["031_workflow_structured_stage"], True),
        (["032_normal_oe_note_pins"], True),
        (["033_workflow_branch_drafts"], None),
        (["future"], None),
        ([], None),
        (["030_declared_upload_basket", "032_normal_oe_note_pins"], None),
    ],
)
def test_dataset_inventory_requires_exact_installed_schema(revisions, expected):
    from backend.app.declared_upload_capacity import dataset_inventory_enabled

    session = SimpleNamespace(scalars=lambda *a: SimpleNamespace(all=lambda: revisions))
    if expected is None:
        with pytest.raises(DomainError, match="supported schema"):
            dataset_inventory_enabled(session)
    else:
        assert dataset_inventory_enabled(session) is expected


def test_actual_controller_fixture_uses_owned_dataset_namespace(tmp_path):
    """Reproduce former physical rejection without Docker, SQL or a controller."""
    import ast
    from pathlib import Path

    owner, dataset = uuid4(), uuid4()
    source = Path("tests/integration/rd_controller_fixture.py").read_text()
    tree = ast.parse(source)
    keywords = [
        k
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "DatasetVersion"
        for k in n.keywords
        if k.arg == "storage_key"
    ]
    assert len(keywords) == 1
    key = eval(
        compile(ast.Expression(keywords[0].value), "fixture-key", "eval"),
        {"str": str, "owner_id": owner, "dataset_id": dataset},
    )
    assert key == f"datasets/{owner}/{dataset}"
    (tmp_path / "factors").mkdir()
    bad = tmp_path / "datasets" / f"maintained-{dataset}"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_bytes(b"{}")
    fd = os.open(tmp_path / "factors", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(DomainError, match="Structured demo inventory"):
            factor_services.physical_inventory(
                fd, owner, time.monotonic() + 5, 100, storage_root=tmp_path
            )
        good = tmp_path / key
        good.parent.mkdir()
        bad.rename(good)
        assert factor_services.physical_inventory(
            fd, owner, time.monotonic() + 5, 100, storage_root=tmp_path
        ) == (0, 0, 0)
        total, owned, count = factor_services.physical_inventory(
            fd,
            owner,
            time.monotonic() + 5,
            100,
            storage_root=tmp_path,
            include_dataset_inventory=True,
        )
        assert (total, owned, count) == (2, 2, 3)
    finally:
        os.close(fd)


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
def test_internal_publishers_include_actual_orphan_dataset_bytes(
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
        path = tmp_path / "datasets" / str(who) / str(uuid4())
        path.mkdir(parents=True)
        (path / "uncommitted").write_bytes(b"x" * size)
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


@pytest.mark.parametrize(
    "revision",
    [
        "027c_seeded_controller_v4",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
    ],
)
def test_mixed_demo_and_declared_upload_inventory_preserves_old_accounting(
    tmp_path, revision
):
    from backend.app.declared_upload_capacity import dataset_inventory_enabled
    from packages.structured_factor_strategy.demo import dataset_id, encoded_demo_market

    owner, other = uuid4(), uuid4()
    market = encoded_demo_market()
    demo_bytes = {"manifest.json": market.manifest_bytes, **dict(market.artifacts)}
    size = sum(map(len, demo_bytes.values()))
    (tmp_path / "factors").mkdir()
    for who, upload_size in ((owner, 13), (other, 29)):
        demo = tmp_path / "datasets" / str(who) / str(dataset_id(who))
        demo.mkdir(parents=True)
        for name, raw in demo_bytes.items():
            (demo / name).write_bytes(raw)
        upload = demo.parent / str(uuid4()) / "sources"
        upload.mkdir(parents=True)
        (upload / "uncommitted.csv").write_bytes(b"x" * upload_size)
    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision])
    )
    enabled = dataset_inventory_enabled(session)
    fd = os.open(tmp_path / "factors", os.O_RDONLY | os.O_DIRECTORY)
    try:
        total, owned, count = factor_services.physical_inventory(
            fd,
            owner,
            time.monotonic() + 5,
            100,
            storage_root=tmp_path,
            include_dataset_inventory=enabled,
        )
        assert (total, owned) == (
            (2 * size + 42, size + 13) if enabled else (2 * size, size)
        )
        assert count == (20 if enabled else 16)
        with pytest.raises(DomainError):
            factor_services.physical_inventory(
                fd,
                owner,
                time.monotonic() - 1,
                100,
                storage_root=tmp_path,
                include_dataset_inventory=enabled,
            )
        with pytest.raises(DomainError):
            factor_services.physical_inventory(
                fd,
                owner,
                time.monotonic() + 5,
                1,
                storage_root=tmp_path,
                include_dataset_inventory=enabled,
            )
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "revision, closure",
    [
        ("032_normal_oe_note_pins", None),
        ("032_normal_oe_note_pins", "future"),
        ("027c_seeded_controller_v4", "mineagent.declared-upload-restore-closure.v1"),
    ],
)
def test_dataset_inventory_rejects_mismatched_capability(
    monkeypatch, revision, closure
):
    from backend.app import schema_capabilities
    from backend.app.declared_upload_capacity import dataset_inventory_enabled

    monkeypatch.setattr(
        schema_capabilities,
        "capabilities_for",
        lambda value: SimpleNamespace(declared_upload_schema=closure),
    )
    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision])
    )
    with pytest.raises(DomainError, match="capability differs"):
        dataset_inventory_enabled(session)
