import hashlib
import json
import os
import time
from uuid import UUID

import pytest

from backend.app import factor_native_storage as native
from backend.app.factor_services import OBJECT_TYPES, physical_inventory
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.factors.v2_evidence import EvidenceObject
from packages.factors.v2_storage_contracts import storage_identity
from tests.factors.test_v2_storage_contracts import fixture

OWNER = UUID(int=1)


def put(
    root, data=b"opaque, deliberately not parsed as HDF", *, owner=OWNER, suffix="h5"
):
    sha = hashlib.sha256(data).hexdigest()
    path = root / f"factors/{owner}/objects/{sha}.{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, storage_identity(
        owner,
        EvidenceObject(
            role="native_hdf",
            object_id=UUID(int=3),
            object_kind="native_hdf",
            media_type="application/x-hdf5",
            sha256=sha,
            size_bytes=len(data),
        ),
    )


def test_opaque_inventory_counts_all_owners_and_retains_orphans(tmp_path):
    _, identity = put(tmp_path)
    orphan, _ = put(tmp_path, b"another opaque native result", owner=UUID(int=2))
    rows = native.inventory(tmp_path)
    assert (
        len(rows) == 6
        and sum(not row.directory for row in rows) == 2
        and sum(row.size_bytes for row in rows)
        == identity.object.size_bytes + orphan.stat().st_size
    )
    extra = native.verify_native_physical_closure(tmp_path, [identity])
    assert len(extra) == 1 and extra[0].owner_id == UUID(int=2)
    assert orphan.read_bytes() == b"another opaque native result"


def test_active_old_inventory_and_revisions_still_reject_native(tmp_path):
    put(tmp_path)
    fd = os.open(tmp_path / "factors", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(DomainError, match="Unknown factor object name"):
            physical_inventory(fd, OWNER, time.monotonic() + 2, 100)
    finally:
        os.close(fd)
    assert "native_hdf" not in OBJECT_TYPES
    for revision in ("015_neutral_primitives", "016_process_versioning"):
        assert (
            capabilities_for(revision).factor_closure_schema
            == "mineagent.factor-restore-closure.v2"
        )


@pytest.mark.parametrize("kind", ["tmp", "journal", "journal_tmp", "empty_staging"])
def test_all_unsettled_files_and_empty_operations_are_recovery_barriers(tmp_path, kind):
    path, identity = put(tmp_path)
    if kind == "tmp":
        target = path.with_name("." + path.name + "." + "a" * 32 + ".tmp")
    else:
        folder = tmp_path / f"factors/{OWNER}/staging/{UUID(int=7)}"
        folder.mkdir(parents=True)
        target = folder / (
            ".publication.v2.json." + "a" * 32 + ".tmp"
            if kind == "journal_tmp"
            else "publication.v2.json"
        )
    if kind != "empty_staging":
        target.write_bytes(b'{"observed_state":"recorded"}')
    rows = native.inventory(tmp_path)
    assert any(row.recovery_barrier for row in rows)
    assert sum(row.size_bytes for row in rows) == path.stat().st_size + (
        0 if kind == "empty_staging" else target.stat().st_size
    )
    with pytest.raises(ValueError, match="recovery required"):
        native.verify_native_physical_closure(tmp_path, [identity])
    assert path.exists()
    if kind != "empty_staging":
        assert target.exists()


@pytest.mark.parametrize(
    "mode",
    [
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "unknown_suffix",
        "wrong_hash",
        "oversized",
        "owner_alias",
    ],
)
def test_unsafe_or_ambiguous_physical_entries_fail_closed(tmp_path, mode):
    path, _ = put(tmp_path)
    if mode == "symlink":
        path.unlink()
        path.symlink_to("/etc/hosts")
    elif mode == "hardlink":
        os.link(path, path.with_name("linked.h5"))
    elif mode == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif mode == "directory":
        path.unlink()
        path.mkdir()
    elif mode == "unknown_suffix":
        path.rename(path.with_suffix(".pickle"))
    elif mode == "wrong_hash":
        path.write_bytes(b"changed")
    elif mode == "oversized":
        with path.open("wb") as stream:
            stream.truncate(native.MAX_NATIVE_BYTES + 1)
    elif mode == "owner_alias":
        path.parent.parent.rename(path.parent.parent.with_name(OWNER.hex))
    with pytest.raises((ValueError, OSError)):
        native.inventory(tmp_path)


@pytest.mark.parametrize("change", ["missing", "owner", "size", "hash", "duplicate"])
def test_registration_drift_and_duplicates_are_rejected(tmp_path, change):
    path, identity = put(tmp_path)
    if change == "missing":
        path.unlink()
    raw = identity.model_dump(mode="json")
    if change == "owner":
        raw["owner_id"] = str(UUID(int=4))
        raw["storage_key"] = raw["storage_key"].replace(str(OWNER), str(UUID(int=4)))
    elif change == "size":
        raw["object"]["size_bytes"] += 1
    elif change == "hash":
        raw["object"]["sha256"] = "0" * 64
        raw["storage_key"] = (
            raw["storage_key"].rsplit("/", 1)[0] + "/" + "0" * 64 + ".h5"
        )
    with pytest.raises(ValueError):
        native.verify_native_physical_closure(
            tmp_path, [raw, raw] if change == "duplicate" else [raw]
        )


@pytest.mark.parametrize("bound", [0, -1, True, 10001])
def test_entry_bounds_are_closed(tmp_path, bound):
    put(tmp_path)
    with pytest.raises(ValueError):
        native.inventory(tmp_path, max_entries=bound)


def test_byte_and_directory_budgets_do_not_ignore_orphan_bytes(tmp_path):
    put(tmp_path)
    with pytest.raises(ValueError):
        native.inventory(tmp_path, max_bytes=1)
    with pytest.raises(ValueError):
        native.inventory(tmp_path, max_entries=2)


def test_conservative_peak_includes_journal_temps_finals_and_directories():
    _, _, journal = fixture()
    footprint = native.publication_footprint(journal)
    assert footprint.object_peak_bytes == journal.declared_object_peak_bytes
    assert footprint.journal_peak_bytes == 2 * len(canonical_bytes(journal))
    assert (
        footprint.peak_bytes
        == footprint.object_peak_bytes + footprint.journal_peak_bytes
    )
    assert footprint.peak_entries == 2 * len(journal.inventory.objects) + 6


def test_no_mutation_or_native_parser_api_is_present():
    from pathlib import Path

    source = Path(native.__file__).read_text()
    for forbidden in (
        "import h5py",
        "import pandas",
        "pickle.",
        "os.unlink(",
        "os.remove(",
        ".commit(",
        ".rollback(",
        "publish_bytes(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize("timeout", [0, -1, True, 31, float("nan"), float("inf")])
def test_time_ceiling_rejects_invalid_bounds(tmp_path, timeout):
    put(tmp_path)
    with pytest.raises(ValueError):
        native.inventory(tmp_path, timeout_seconds=timeout)


def test_expired_inventory_is_not_a_partial_success(tmp_path, monkeypatch):
    put(tmp_path)
    clock = iter([0, 31])
    monkeypatch.setattr(native.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="time ceiling"):
        native.inventory(tmp_path)


@pytest.mark.parametrize("mutation", ["file", "root"])
def test_concurrent_file_and_root_replacement_fail_closed(
    tmp_path, monkeypatch, mutation
):
    path, _ = put(tmp_path)
    real_read = native.os.read
    changed = False

    def raced_read(fd, size):
        nonlocal changed
        data = real_read(fd, size)
        if data and not changed:
            changed = True
            if mutation == "file":
                path.write_bytes(b"changed")
            else:
                (tmp_path / "factors").rename(tmp_path / "old-factors")
                (tmp_path / "factors").mkdir()
        return data

    monkeypatch.setattr(native.os, "read", raced_read)
    with pytest.raises(ValueError):
        native.inventory(tmp_path)


def test_partial_temporary_object_is_counted_not_hash_promoted(tmp_path):
    path, _ = put(tmp_path)
    temp = path.with_name("." + path.name + "." + "b" * 32 + ".tmp")
    temp.write_bytes(b"partial")
    row = next(r for r in native.inventory(tmp_path) if r.temporary)
    assert row.size_bytes == 7 and row.sha256 == hashlib.sha256(b"partial").hexdigest()
    assert row.recovery_barrier


def candidate_intent():
    from packages.factors.native_publication_plan import CandidateNativeIntent

    evidence, _, journal = fixture()
    native_object = journal.inventory.objects[1]
    process = evidence.cutoffs[0].candidate
    from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2

    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=process.admission_id,
        **{
            key: getattr(process.binding, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        role="factor_cutoff_candidate",
        logical_operation_id=process.cutoff_execution_id,
        launch_ordinal=1,
        profile=process.profile,
        image_id=process.image,
        input_sha256=process.input_sha256,
        reservation_id=UUID(int=97),
        ipc={"ipc_policy": "none", "ticket_sha256": None},
    )
    return CandidateNativeIntent(
        schema_version="mineagent.candidate-native-intent.v1",
        journal_id=UUID(int=99),
        reservation_id=UUID(int=98),
        process_role="factor_cutoff_candidate",
        admission=admission,
        source_object_id=UUID(int=96),
        stop_receipt_id=UUID(int=95),
        extraction_receipt_id=UUID(int=94),
        process=evidence.cutoffs[0].candidate,
        output=native_object,
        temporary=journal.temporary_objects[1],
        journal_key=f"factors/{native_object.owner_id}/staging/{UUID(int=99)}/candidate-native.v1.json",
    )


def test_candidate_intent_does_not_require_future_normalizer_or_stitched_object():
    intent = candidate_intent()
    raw = canonical_bytes(intent)
    assert b"normalizer" not in raw and b"stitched" not in raw
    footprint = native.candidate_publication_footprint(intent)
    assert footprint.object_peak_bytes == 2 * intent.output.object.size_bytes
    assert footprint.journal_peak_bytes == 2 * len(raw)
    assert footprint.peak_entries == 8
    assert intent.observed_state == "pending"


@pytest.mark.parametrize(
    "change", ["role", "owner", "output", "temporary", "path", "recorded", "receipt"]
)
def test_candidate_intent_cannot_substitute_identity_or_claim_commit(change):
    from packages.factors.native_publication_plan import CandidateNativeIntent

    raw = candidate_intent().model_dump(mode="json")
    if change == "role":
        raw["process_role"] = "factor_normalizer"
    elif change == "owner":
        raw["process"]["binding"]["owner_id"] = str(UUID(int=202))
    elif change == "output":
        raw["process"]["output"]["sha256"] = "0" * 64
    elif change == "temporary":
        raw["temporary"]["final"]["object"]["object_id"] = str(UUID(int=202))
    elif change == "path":
        raw["journal_key"] += ".tmp"
    elif change == "recorded":
        raw["observed_state"] = "recorded"
    else:
        raw["commit_receipt"] = "a" * 64
    with pytest.raises(ValueError):
        CandidateNativeIntent.model_validate_json(json.dumps(raw))


def test_candidate_intent_journal_is_always_a_recovery_barrier(tmp_path):
    path, identity = put(tmp_path)
    journal = (
        tmp_path / f"factors/{OWNER}/staging/{UUID(int=99)}/candidate-native.v1.json"
    )
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b"opaque untrusted intent bytes")
    records = native.inventory(tmp_path)
    assert next(
        r for r in records if r.storage_key.endswith("candidate-native.v1.json")
    ).recovery_barrier
    with pytest.raises(ValueError):
        native.verify_native_physical_closure(tmp_path, [identity])
    assert path.exists() and journal.exists()


def test_final_eof_deadline_is_checked_even_after_last_data_chunk(
    tmp_path, monkeypatch
):
    put(tmp_path)
    clock = [0]
    read = native.os.read

    def delayed_eof(fd, size):
        result = read(fd, size)
        if not result:
            clock[0] = 10
        return result

    monkeypatch.setattr(native.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(native.os, "read", delayed_eof)
    with pytest.raises(ValueError, match="time ceiling"):
        native.inventory(tmp_path)


def test_all_observed_directory_entries_are_available_for_quota_accounting(tmp_path):
    put(tmp_path)
    rows = native.inventory(tmp_path)
    assert len(rows) == 3 and sum(row.directory for row in rows) == 2
    assert all(
        row.sha256 is None and row.size_bytes == 0 for row in rows if row.directory
    )


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "logical_operation_id",
        "profile",
        "image_id",
        "input_sha256",
        "role",
    ],
)
def test_candidate_intent_binds_every_persisted_admission_identity(field):
    from packages.factors.native_publication_plan import CandidateNativeIntent

    intent = candidate_intent()
    assert CandidateNativeIntent.model_validate_json(intent.model_dump_json()) == intent
    raw = intent.model_dump(mode="json")
    raw["admission"][field] = (
        2
        if field == "generation"
        else "wrong.v2"
        if field == "profile"
        else "sha256:" + "f" * 64
        if field == "image_id"
        else "f" * 64
        if field == "input_sha256"
        else "factor_normalizer"
        if field == "role"
        else str(UUID(int=888))
    )
    with pytest.raises(ValueError):
        CandidateNativeIntent.model_validate_json(json.dumps(raw))


def test_candidate_intent_rejects_controller_ipc_and_same_receipt_identity():
    from packages.factors.native_publication_plan import CandidateNativeIntent

    for change in ("ipc", "receipts"):
        raw = candidate_intent().model_dump(mode="json")
        if change == "ipc":
            raw["admission"]["ipc"] = {
                "ipc_policy": "host_ticket",
                "ticket_sha256": "a" * 64,
            }
        else:
            raw["stop_receipt_id"] = raw["extraction_receipt_id"]
        with pytest.raises(ValueError):
            CandidateNativeIntent.model_validate_json(json.dumps(raw))


def test_publication_reference_closure_is_exact_but_not_stop_authority():
    from packages.factors.contracts import digest
    from packages.factors.native_publication_plan import (
        CandidateNativePublicationWitness,
        validate_candidate_publication_references,
    )

    intent = candidate_intent()
    witness = CandidateNativePublicationWitness(
        schema_version="mineagent.candidate-native-publication-witness.v1",
        intent_sha256=digest(intent),
        journal_sha256=digest(intent),
        publication_id=UUID(int=66),
        outcome_id=UUID(int=67),
        object=intent.output,
        physical_receipt_sha256="c" * 64,
    )
    assert validate_candidate_publication_references(intent, witness) is None
    for field in ("intent_sha256", "journal_sha256", "object"):
        raw = witness.model_dump(mode="json")
        if field == "object":
            raw["object"]["object"]["object_id"] = str(UUID(int=999))
        else:
            raw[field] = "f" * 64
        with pytest.raises(ValueError):
            validate_candidate_publication_references(intent, raw)
    from packages.research_runtime.process_contracts import ProcessError
    from packages.research_runtime.process_declarations_v2 import (
        require_v2_launch_authority,
    )

    with pytest.raises(ProcessError):
        require_v2_launch_authority(intent.admission)


def test_generic_legacy_backup_retains_orphans_without_semantic_registration(tmp_path):
    from backend.app.operations_backup import BackupLimits, _inventory

    path, _ = put(tmp_path, b"orphan")
    entries, _ = _inventory(tmp_path, BackupLimits(), storage=True)
    assert len(entries) == 1
    assert entries[0]["path"] == path.relative_to(tmp_path).as_posix()
    assert entries[0]["sha256"] == hashlib.sha256(b"orphan").hexdigest()
    assert "native_hdf" not in OBJECT_TYPES


def test_candidate_storage_reservation_cannot_reuse_process_reservation():
    from packages.factors.native_publication_plan import CandidateNativeIntent

    raw = candidate_intent().model_dump(mode="json")
    raw["reservation_id"] = raw["admission"]["reservation_id"]
    with pytest.raises(ValueError, match="reservations must be distinct"):
        CandidateNativeIntent.model_validate_json(json.dumps(raw))
