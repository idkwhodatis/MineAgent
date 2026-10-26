"""Real local opaque filesystem protocol; not PostgreSQL/launch acceptance."""

import hashlib
import os
from datetime import UTC

import pytest

from backend.app import factor_native_io as io
from packages.factors.native_publication import NativePublicationIntent
from tests.factors.test_native_physical_closure import candidate_intent


def real_intent():
    data = b"opaque bytes, deliberately not a host parsed HDF file"
    from datetime import datetime

    previous = candidate_intent()
    binding = previous.process.binding
    value = {
        "schema_version": "mineagent.candidate-native-intent.v2",
        "journal_id": previous.journal_id,
        "reservation_id": previous.reservation_id,
        "admission": previous.admission,
        "source_id": binding.generation_source_id,
        "generation_start_id": binding.generation_start_id,
        "factor_version_id": binding.factor_version_id,
        "source_object_id": previous.source_object_id,
        "source_sha256": binding.source_sha256,
        "prefix_sha256": previous.process.prefix_sha256,
        "snapshot_input_sha256": binding.snapshot_input_sha256,
        "cutoff": datetime(2020, 1, 1, tzinfo=UTC),
        "stop_receipt_id": previous.stop_receipt_id,
        "stop_receipt_sha256": previous.process.stop_receipt_sha256,
        "extraction_receipt_id": previous.extraction_receipt_id,
        "extraction_receipt_sha256": previous.process.extraction_receipt_sha256,
        "output": previous.output.model_dump(),
        "journal_key": f"factors/{previous.output.owner_id}/native-journals/{previous.journal_id}.json",
    }
    sha = hashlib.sha256(data).hexdigest()
    obj = value["output"]["object"]
    obj.update(sha256=sha, size_bytes=len(data))
    value["output"]["storage_key"] = (
        f"factors/{value['output']['owner_id']}/objects/{sha}.h5"
    )
    return NativePublicationIntent.model_validate(value), data


def test_atomic_opaque_publish_replay_and_retained_guard(tmp_path):
    intent, data = real_intent()
    (tmp_path / "factors").mkdir()
    with io.native_storage_guard(tmp_path):
        with pytest.raises(BlockingIOError), io.native_storage_guard(tmp_path):
            pytest.fail("Concurrent publisher entered")
        io.publish_candidate_bytes(tmp_path, intent, data)
        io.publish_candidate_bytes(tmp_path, intent, data)
        assert io.verify_candidate_bytes(tmp_path, intent) == data
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("mutation", ["hash", "size", "symlink", "hardlink", "fifo"])
def test_exact_read_refuses_unsafe_or_corrupt_opaque_output(tmp_path, mutation):
    intent, data = real_intent()
    (tmp_path / "factors").mkdir()
    io.publish_candidate_bytes(tmp_path, intent, data)
    target = tmp_path / intent.output.storage_key
    if mutation == "hash":
        target.chmod(0o600)
        target.write_bytes(b"x" * len(data))
    elif mutation == "size":
        target.chmod(0o600)
        target.write_bytes(data + b"x")
    else:
        target.unlink()
        other = tmp_path / "other"
        other.write_bytes(data)
        if mutation == "symlink":
            target.symlink_to(other)
        elif mutation == "hardlink":
            os.link(other, target)
        else:
            os.mkfifo(target)
    with pytest.raises((OSError, io.NativeStorageError)):
        io.verify_candidate_bytes(tmp_path, intent)
    assert target.exists()  # Never remove uncertain evidence on failure.


def test_lost_second_write_keeps_journal_for_recovery(tmp_path, monkeypatch):
    intent, data = real_intent()
    (tmp_path / "factors").mkdir()
    original = io.publish_bytes
    count = 0

    def fail(target, *args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected lost writer")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(io, "publish_bytes", fail)
    with pytest.raises(OSError):
        io.publish_candidate_bytes(tmp_path, intent, data)
    assert (tmp_path / intent.journal_key).is_file()
    assert not (tmp_path / intent.output.storage_key).exists()


def test_new_namespace_does_not_masquerade_as_parser_stop(tmp_path):
    from runtimes.factor_parser.supervisor import assert_quiescent

    intent, data = real_intent()
    (tmp_path / "factors").mkdir()
    io.publish_candidate_bytes(tmp_path, intent, data)
    assert_quiescent(tmp_path)
    assert not list(tmp_path.rglob("staging"))


def test_physical_quota_opt_in_counts_journal_object_and_directories(tmp_path):
    import time

    from backend.app.factor_services import physical_inventory
    from backend.app.services import DomainError
    from packages.factors.contracts import canonical_bytes

    intent, data = real_intent()
    (tmp_path / "factors").mkdir()
    io.publish_candidate_bytes(tmp_path, intent, data)
    with io.native_storage_guard(tmp_path) as fd:
        with pytest.raises(DomainError):
            physical_inventory(fd, intent.admission.owner_id, time.monotonic() + 5, 100)
        entries = []
        total, owned, count = physical_inventory(
            fd,
            intent.admission.owner_id,
            time.monotonic() + 5,
            100,
            native=True,
            native_entries=entries,
        )
    assert total == owned == len(data) + len(canonical_bytes(intent))
    assert count == 5  # owner, objects, native-journals, two final files
    assert {key for key, _ in entries} == {
        intent.output.storage_key,
        intent.journal_key,
    }


def test_native_v2_has_no_aggregate_or_caller_stop_authority():
    from pydantic import ValidationError

    intent, _ = real_intent()
    value = intent.model_dump()
    for field in ("evaluation_id", "observed_state", "verified", "snapshot_id"):
        with pytest.raises(ValidationError):
            NativePublicationIntent.model_validate({**value, field: "claimed"})


def test_native_schema_defaults_to_closed_on_historical_revisions():
    from backend.app.factor_native_services import native_capability

    class SessionDouble:
        def __init__(self, revision):
            self.revision = revision

        def scalars(self, query):
            return self

        def all(self):
            return [self.revision]

    for revision in (
        "015_neutral_primitives",
        "016_process_versioning",
        "017_numeric_process_boundary",
    ):
        assert native_capability(SessionDouble(revision)) is False


def test_native_retained_fence_is_not_a_detached_grant(tmp_path):
    from backend.app.factor_native_io import (
        NativeStorageError,
        native_storage_guard,
        require_native_storage_guard,
    )

    (tmp_path / "factors").mkdir()
    with pytest.raises(NativeStorageError, match="Retained"):
        require_native_storage_guard(tmp_path)
    with native_storage_guard(tmp_path) as fd:
        assert require_native_storage_guard(tmp_path) == fd
        with pytest.raises(NativeStorageError, match="Retained"):
            require_native_storage_guard(tmp_path / "other")
    with pytest.raises(NativeStorageError, match="Retained"):
        require_native_storage_guard(tmp_path)
