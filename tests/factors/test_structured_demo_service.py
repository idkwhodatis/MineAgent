"""Real immutable files/readers, simulated SQL/maintenance authority only."""

import ast
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import structured_demo_services as service
from backend.app.core.maintenance import _lifecycle
from backend.app.engine_run_preflight import RECOMMENDED_BUDGET, preflight
from backend.app.engine_run_submission_schemas import RDSubmissionRequest
from backend.app.factor_services import PublicationAdmission, physical_inventory
from backend.app.models import User
from backend.app.services import DomainError
from backend.app.structured_demo_storage import demo_physical_usage
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.structured_factor_strategy.demo import (
    dataset_id,
    encoded_demo_market,
    rd_preset,
)


@pytest.fixture(scope="module")
def demo():
    return encoded_demo_market()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    (tmp_path / "factors").mkdir()
    owner = uuid4()
    engine = object()
    rows = {}
    users = {owner}
    locks = []

    class Session:
        is_active = True

        def get_bind(self):
            return engine

        def connection(self):
            return SimpleNamespace(invalidated=False)

        def get(self, kind, identity):
            return (
                SimpleNamespace(id=identity, is_active=True)
                if kind is User and identity in users
                else rows.get(identity)
            )

        def scalar(self, query):
            values = query.compile().params.values()
            return next(
                (r for r in rows.values() if r.owner_id in values and r.id in values),
                None,
            )

        def scalars(self, query):
            return SimpleNamespace(all=lambda: ["025_structured_factor_strategy"])

        def add(self, row):
            rows[row.id] = row

        def flush(self):
            pass

        def commit(self):
            raise AssertionError("Service must not commit")

        def rollback(self):
            raise AssertionError("Service must not rollback")

    monkeypatch.setattr(service, "try_lock", lambda session, scope: locks.append(scope))
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    factors = os.open(tmp_path / "factors", os.O_RDONLY | os.O_DIRECTORY)
    token = _lifecycle.set((engine, tmp_path))
    h = SimpleNamespace(
        root=tmp_path,
        owner=owner,
        rows=rows,
        users=users,
        session=Session(),
        locks=locks,
        settings=SimpleNamespace(storage_root=tmp_path, rdagent_research_enabled=False),
        admission=PublicationAdmission(time.monotonic() + 20, root, factors),
        root_fd=root,
        factor_fd=factors,
    )
    try:
        yield h
    finally:
        _lifecycle.reset(token)
        os.close(root)
        os.close(factors)


def publish(h):
    return service.publish_demo_dataset(
        h.session, h.owner, h.settings, admission=h.admission
    )


def test_real_owner_idempotence_and_normal_preflight(harness):
    h = harness
    row = publish(h)
    before = {p.name: p.read_bytes() for p in (h.root / row.storage_key).iterdir()}
    assert publish(h) is row and len(h.rows) == 1
    assert before == {
        p.name: p.read_bytes() for p in (h.root / row.storage_key).iterdir()
    }
    assert row.id == dataset_id(h.owner) and row.is_synthetic
    preset = rd_preset()
    assert preset.pop("acknowledgement_required") is True
    assert preset.pop("qualification").startswith("synthetic")
    request = RDSubmissionRequest.model_validate(
        {
            **preset,
            "dataset_version_id": row.id,
            "budget": RECOMMENDED_BUDGET,
            "source_policy": ConfiguredSourcePolicy(acknowledged_unverified=True),
        }
    )
    result = preflight(h.session, h.owner, request, h.settings)
    assert result.data_eligible and result.cutoff_count == 1
    assert not result.can_submit and [r.code for r in result.reasons] == [
        "runtime_disabled"
    ]
    assert h.locks == [f"submit:{h.owner}"] * 2


def test_demo_bytes_count_in_same_factor_inventory_for_owner_and_installation(harness):
    h = harness
    row = publish(h)
    actual = sum(p.stat().st_size for p in (h.root / row.storage_key).iterdir())
    assert demo_physical_usage(h.root_fd, h.owner, time.monotonic() + 5, 100) == (
        actual,
        actual,
        8,
    )
    assert demo_physical_usage(h.root_fd, uuid4(), time.monotonic() + 5, 100) == (
        actual,
        0,
        8,
    )
    assert physical_inventory(
        h.factor_fd, h.owner, time.monotonic() + 5, 100, storage_root=h.root
    ) == (actual, actual, 8)
    with pytest.raises(DomainError):
        physical_inventory(
            h.factor_fd, h.owner, time.monotonic() + 5, 7, storage_root=h.root
        )


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "symlink", "corrupt", "marker", "hardlink"]
)
def test_corrupt_or_uncertain_demo_tree_blocks_inventory_and_never_repairs(
    harness, mutation
):
    h = harness
    row = publish(h)
    directory = h.root / row.storage_key
    leaf = directory / "preview.json"
    if mutation == "extra":
        (directory / "unknown.json").write_bytes(b"{}")
    if mutation == "missing":
        leaf.unlink()
    if mutation == "symlink":
        leaf.unlink()
        leaf.symlink_to(h.root / "missing")
    if mutation == "corrupt":
        leaf.chmod(0o600)
        leaf.write_bytes(b"[]")
    if mutation == "marker":
        path = directory / "manifest.json"
        value = json.loads(path.read_bytes())
        value.pop("structured_demo")
        path.chmod(0o600)
        path.write_text(json.dumps(value))
    if mutation == "hardlink":
        os.link(leaf, h.root / "extra-link")
    with pytest.raises((ValueError, OSError)):
        demo_physical_usage(h.root_fd, h.owner, time.monotonic() + 5, 100)
    assert directory.exists()


def test_partial_publication_is_not_adopted_or_deleted(harness, monkeypatch):
    h = harness
    original = service.publish_bytes
    written = []

    def broken(path, *args, **kwargs):
        if written:
            raise OSError("simulated interruption")
        original(path, *args, **kwargs)
        written.append(path)

    monkeypatch.setattr(service, "publish_bytes", broken)
    with pytest.raises(OSError):
        publish(h)
    assert not h.rows and written[0].exists()
    monkeypatch.setattr(service, "publish_bytes", original)
    with pytest.raises(DomainError, match="explicit recovery"):
        publish(h)
    assert written[0].exists() and not h.rows


def test_missing_caller_maintenance_fence_prevents_any_write(harness):
    h = harness
    token = _lifecycle.set(None)
    try:
        with pytest.raises(DomainError, match="maintenance lifecycle"):
            publish(h)
    finally:
        _lifecycle.reset(token)
    assert not h.rows and not (h.root / "datasets").exists()


def test_foreign_owner_never_reuses_authenticated_owner_identity(harness):
    h = harness
    with pytest.raises(DomainError, match="inactive user"):
        service.publish_demo_dataset(
            h.session, uuid4(), h.settings, admission=h.admission
        )
    assert not h.rows


def test_legacy_factor_dataset_names_remain_closed(harness):
    h = harness
    directory = h.root / "factors" / str(h.owner) / "datasets" / str(uuid4())
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(b"{}")
    (directory / "preview.json").write_bytes(b"[]")
    with pytest.raises(DomainError, match="Unknown fixture dataset file"):
        physical_inventory(
            h.factor_fd, h.owner, time.monotonic() + 5, 100, storage_root=h.root
        )


def test_all_production_factor_inventory_callers_supply_storage_root():
    root = Path(__file__).resolve().parents[2]
    paths = [
        "factor_services.py",
        "factor_native_services.py",
        "execution_snapshot_services.py",
        "rdagent_source_publication.py",
    ]
    found = 0
    for name in paths:
        tree = ast.parse((root / "backend/app" / name).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "physical_inventory"
            ):
                found += 1
                assert any(k.arg == "storage_root" for k in node.keywords), name
    assert found == 5


def test_two_active_owners_get_distinct_rows_and_physical_namespaces(harness):
    h = harness
    first = publish(h)
    original_owner = h.owner
    h.owner = uuid4()
    h.users.add(h.owner)
    second = publish(h)
    assert first.id != second.id and first.owner_id != second.owner_id
    assert first.content_sha256 == second.content_sha256
    assert first.storage_key != second.storage_key
    total, owned, _ = demo_physical_usage(
        h.root_fd, original_owner, time.monotonic() + 5, 100
    )
    assert total == 2 * owned and owned > 0


@pytest.mark.parametrize("limit", ["owner", "installation"])
def test_existing_publication_quota_rejects_demo_bytes_before_yield(
    harness, monkeypatch, limit
):
    from backend.app import (
        execution_snapshot_capacity,
        factor_native_services,
        factor_services,
        rdagent_source_capacity,
    )
    from runtimes.factor_parser import supervisor

    h = harness
    publish(h)
    monkeypatch.setattr(factor_services, "try_lock", lambda *a: None)
    monkeypatch.setattr(
        factor_native_services, "native_capability", lambda session: False
    )
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
        scalar=lambda *a, **k: h.owner,
        scalars=lambda *a: SimpleNamespace(all=lambda: ["029_research_workflow"]),
    )
    settings = SimpleNamespace(
        storage_root=h.root,
        factor_library_enabled=True,
        factor_max_files=100,
        factor_owner_max_bytes=1 if limit == "owner" else 1000000,
        factor_installation_max_bytes=1 if limit == "installation" else 1000000,
    )
    with (
        pytest.raises(DomainError, match="physical quota exceeded"),
        factor_services.publication(session, h.owner, settings, peak_bytes=1),
    ):
        raise AssertionError("Over-quota publisher must never receive admission")


def test_unrelated_legacy_dataset_is_not_reclassified_as_demo(harness):
    h = harness
    path = h.root / "datasets" / str(h.owner) / str(uuid4())
    path.mkdir(parents=True)
    (path / "arbitrary-legacy.csv").write_bytes(b"legacy bytes")
    assert demo_physical_usage(h.root_fd, h.owner, time.monotonic() + 5, 100) == (
        0,
        0,
        0,
    )


@pytest.mark.parametrize("level", ["datasets", "owner", "demo"])
def test_quota_inventory_refuses_symlinked_demo_ancestors(harness, level):
    h = harness
    target = h.root / "elsewhere"
    target.mkdir()
    datasets = h.root / "datasets"
    if level == "datasets":
        datasets.symlink_to(target, target_is_directory=True)
    else:
        datasets.mkdir()
        owner = datasets / str(h.owner)
        if level == "owner":
            owner.symlink_to(target, target_is_directory=True)
        else:
            owner.mkdir()
            (owner / str(dataset_id(h.owner))).symlink_to(
                target, target_is_directory=True
            )
    with pytest.raises(OSError):
        demo_physical_usage(h.root_fd, h.owner, time.monotonic() + 5, 100)


def test_production_demo_never_imports_test_fixtures_or_commits_the_session():
    root = Path(__file__).resolve().parents[2]
    for path in (
        "packages/structured_factor_strategy/demo.py",
        "backend/app/structured_demo_services.py",
    ):
        tree = ast.parse((root / path).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("tests", "test_", "rd_configured_dataset_fixture")
                )
            if isinstance(node, ast.Import):
                assert all(
                    not item.name.startswith(("tests", "test_")) for item in node.names
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback", "unlink", "rmdir"}
