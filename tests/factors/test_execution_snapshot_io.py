"""Actual local I/O with synthetic commitments, not publication authority."""

import hashlib
from uuid import uuid4

import pytest

from backend.app.execution_snapshot_io import (
    journal_address,
    publish_snapshot_bytes,
    verify_snapshot_bytes,
)
from backend.app.factor_native_io import native_storage_guard
from packages.factors.contracts import canonical_bytes
from packages.factors.execution_snapshot import (
    ExecutionObject,
    ExecutionPublicationJournal,
    ExecutionSnapshotManifest,
)
from tests.factors.test_execution_snapshot_contract import manifest


def physical_fixture():
    value = manifest().model_dump(mode="json")
    physical = {}
    for item, raw in [
        (value["partitions"][0]["values"], b"opaque parquet fixture"),
        (value["assembled_rows"], b'{"synthetic":true}'),
    ]:
        item["sha256"] = hashlib.sha256(raw).hexdigest()
        item["size_bytes"] = len(raw)
        suffix = "parquet" if item["kind"] == "normalized_values" else "json"
        item["storage_key"] = (
            f"factors/{item['owner_id']}/objects/{item['sha256']}.{suffix}"
        )
        physical[item["storage_key"]] = raw
    document = ExecutionSnapshotManifest.model_validate(value)
    raw = canonical_bytes(document)
    sha = hashlib.sha256(raw).hexdigest()
    obj = ExecutionObject(
        object_id=uuid4(),
        owner_id=document.owner_id,
        kind="execution_manifest",
        sha256=sha,
        size_bytes=len(raw),
        storage_key=f"factors/{document.owner_id}/objects/{sha}.json",
    )
    physical[obj.storage_key] = raw
    journal = ExecutionPublicationJournal(
        schema_version="mineagent.execution-snapshot-publication.v1",
        publication_id=document.snapshot_id,
        reservation_id=uuid4(),
        manifest=document,
        manifest_object=obj,
        peak_bytes=2 * sum(map(len, physical.values())) + 4194304,
        peak_entries=11,
    )
    return journal, physical


def test_snapshot_opaque_files_replay_and_corruption_fail_closed(tmp_path):
    (tmp_path / "factors").mkdir()
    journal, physical = physical_fixture()
    with native_storage_guard(tmp_path):
        publish_snapshot_bytes(tmp_path, journal, physical)
        publish_snapshot_bytes(tmp_path, journal, physical)
        verify_snapshot_bytes(tmp_path, journal)
        key, _sha, raw = journal_address(journal)
        assert (tmp_path / key).read_bytes() == raw
        target = tmp_path / journal.manifest.assembled_rows.storage_key
        target.chmod(0o600)
        target.write_bytes(b"corrupted")
        with pytest.raises(ValueError):
            verify_snapshot_bytes(tmp_path, journal)
    assert (tmp_path / key).exists()


def test_snapshot_io_requires_retained_native_storage_admission(tmp_path):
    journal, physical = physical_fixture()
    with pytest.raises(ValueError, match="Retained"):
        publish_snapshot_bytes(tmp_path, journal, physical)
    assert not (tmp_path / "factors").exists()


def test_snapshot_partial_write_retains_journal_for_charged_recovery(
    tmp_path, monkeypatch
):
    import backend.app.execution_snapshot_io as io

    (tmp_path / "factors").mkdir()
    journal, physical = physical_fixture()
    original = io.publish_bytes
    count = 0

    def interrupt(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected local write interruption")
        return original(*args, **kwargs)

    with native_storage_guard(tmp_path), monkeypatch.context() as patch:
        patch.setattr(io, "publish_bytes", interrupt)
        with pytest.raises(OSError):
            publish_snapshot_bytes(tmp_path, journal, physical)
    key, _, raw = journal_address(journal)
    assert (tmp_path / key).read_bytes() == raw
