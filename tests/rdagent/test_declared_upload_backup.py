"""Real pure assembler + retained bytes and lineage, no PostgreSQL claim."""

import copy
import json
from uuid import UUID

import pytest
from test_declared_upload_source import OWNER, TARGET, source

from backend.app.declared_upload_backup import (
    FIELDS,
    PROFILE,
    SCHEMA,
    closure_for,
    validate_snapshot,
)
from backend.app.schema_capabilities import capabilities_for
from packages.factors.contracts import digest
from packages.rdagent_adapter.declared_upload import build_declared_basket

CAP = capabilities_for("030_declared_upload_basket")


@pytest.fixture
def retained():
    return retained_sources((source(0), source(1)))


def retained_sources(sources):
    built = build_declared_basket(owner_id=OWNER, dataset_id=TARGET, sources=sources)
    blobs, datasets, references = {}, [], []
    for identity, sha, raw, artifacts in [
        *(
            (s.dataset_id, s.manifest_sha256, s.manifest_bytes, s.artifacts)
            for s in sources
        ),
        (TARGET, built.sha256, built.manifest_bytes, dict(built.files)),
    ]:
        key = f"datasets/{OWNER}/{identity}"
        manifest = json.loads(raw)
        blobs[key + "/manifest.json"] = raw
        blobs.update({key + "/" + n: content for n, content in artifacts.items()})
        datasets.append(
            {
                "id": str(identity),
                "owner_id": str(OWNER),
                "market": "US",
                "start_date": "2024-01-02",
                "end_date": "2024-01-04",
                "source": "declared_upload_basket" if identity == TARGET else "upload",
                "is_synthetic": False,
                "content_sha256": sha,
                "manifest": manifest,
                "storage_key": key,
            }
        )
        references.append({"path": key, "sha256": sha, "manifest": manifest})
    head = {
        "id": str(UUID(int=99)),
        "owner_id": str(OWNER),
        "idempotency_key": "test-key",
        "request_sha256": digest(
            {
                "source_dataset_ids": [str(s.dataset_id) for s in sources],
                "acknowledge_unverified": True,
                "idempotency_key": "test-key",
            }
        ),
        "dataset_id": str(TARGET),
        "manifest_sha256": built.sha256,
        "profile": PROFILE,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    members = [
        {
            "basket_id": head["id"],
            "owner_id": str(OWNER),
            "ordinal": i,
            "source_dataset_id": str(s.dataset_id),
            "source_manifest_sha256": s.manifest_sha256,
            "created_at": head["created_at"],
        }
        for i, s in enumerate(sources)
    ]
    closure = {
        "schema_version": SCHEMA,
        "tables": {
            "owners": [{"id": str(OWNER)}],
            "declared_upload_baskets": [head],
            "declared_upload_basket_members": members,
            "datasets": datasets,
        },
    }
    snapshot = {"datasets": references, "declared_upload_closure": closure}

    def read(key, maximum):
        raw = blobs[key]
        if len(raw) > maximum:
            raise ValueError("bounded reader")
        return raw

    return snapshot, blobs, read


def test_exact_retained_basket_and_original_bytes(retained):
    snapshot, _, read = retained
    validate_snapshot(snapshot, CAP, read, 100)


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "hash",
        "id",
        "ordinal",
        "missing",
        "duplicate",
        "request",
        "idempotency",
        "foreign",
        "extent",
        "source_manifest",
        "extra_dataset",
        "missing_head",
        "source_label",
        "target_label",
    ],
)
def test_lineage_cannot_be_reinterpreted(retained, change):
    snapshot, _, read = retained
    tables = snapshot["declared_upload_closure"]["tables"]
    head = tables["declared_upload_baskets"][0]
    member = tables["declared_upload_basket_members"][0]
    if change == "owner":
        member["owner_id"] = str(UUID(int=77))
    elif change == "hash":
        member["source_manifest_sha256"] = "a" * 64
    elif change == "id":
        member["source_dataset_id"] = str(TARGET)
    elif change == "ordinal":
        member["ordinal"] = True
    elif change == "missing":
        tables["declared_upload_basket_members"].pop()
    elif change == "duplicate":
        tables["declared_upload_basket_members"][1] = copy.deepcopy(member)
    elif change == "request":
        head["request_sha256"] = "a" * 64
    elif change == "idempotency":
        head["idempotency_key"] = "bad key"
    elif change == "foreign":
        tables["datasets"][0]["owner_id"] = str(UUID(int=77))
    elif change == "extent":
        tables["datasets"][0]["end_date"] = "2024-01-05"
    elif change == "source_label":
        tables["datasets"][0]["source"] = "synthetic_fixture"
    elif change == "target_label":
        tables["datasets"][-1]["source"] = "upload"
    elif change == "source_manifest":
        tables["datasets"][0]["manifest"] = {}
    elif change == "extra_dataset":
        tables["datasets"].append(copy.deepcopy(tables["datasets"][0]))
    else:
        tables["declared_upload_baskets"].clear()
    with pytest.raises((ValueError, KeyError)):
        validate_snapshot(snapshot, CAP, read, 100)


@pytest.mark.parametrize("copied", [True, False])
@pytest.mark.parametrize(
    "artifact",
    [
        "manifest.json",
        "source.csv",
        "provenance.json",
        "sessions.json",
        "portable-upload-declaration.json",
    ],
)
def test_original_and_copied_bytes_both_required(retained, copied, artifact):
    snapshot, blobs, read = retained
    source_id = snapshot["declared_upload_closure"]["tables"]["datasets"][0]["id"]
    prefix = (
        f"datasets/{OWNER}/{TARGET}/sources/0"
        if copied
        else f"datasets/{OWNER}/{source_id}"
    )
    key = prefix + "/" + artifact
    assert key in blobs
    blobs[key] += b" "
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, CAP, read, 100)


@pytest.mark.parametrize(
    "revision",
    ["027_rd_controller_v3", "028_oe_structured_search", "029_research_workflow"],
)
def test_old_capabilities_reject_basket_even_without_new_closure(retained, revision):
    snapshot, _, read = retained
    with pytest.raises(ValueError):
        closure_for(snapshot, capabilities_for(revision))
    del snapshot["declared_upload_closure"]
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, capabilities_for(revision), read, 100)


def test_new_empty_lineage_valid_and_bounds_enforced(retained):
    empty = {
        "datasets": [],
        "declared_upload_closure": {
            "schema_version": SCHEMA,
            "tables": {k: [] for k in FIELDS},
        },
    }
    validate_snapshot(empty, CAP, lambda *_: pytest.fail("no bytes"), 1)
    snapshot, _, read = retained
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, CAP, read, 1)


@pytest.mark.parametrize(
    "path",
    [
        "../source.csv",
        "/etc/passwd",
        "sources/0/../secret",
        "manifest.json",
        "sources//source.csv",
    ],
)
def test_inventory_rejects_unsafe_paths_before_reader(path):
    from backend.app.declared_upload_backup import retained_artifacts

    fake = {"schema_version": 3, "profile": PROFILE, "artifact_files": {path: "a" * 64}}
    with pytest.raises(ValueError):
        retained_artifacts(
            fake,
            "datasets/owner/id",
            lambda *_: pytest.fail("unsafe read"),
            declared_upload=True,
        )


def test_lineage_owner_must_exist_in_exact_owner_inventory(retained):
    snapshot, _, read = retained
    snapshot["declared_upload_closure"]["tables"]["owners"].clear()
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, CAP, read, 100)


def test_all_database_ranges_cannot_drift_together_away_from_original_bytes(retained):
    snapshot, _, read = retained
    for row in snapshot["declared_upload_closure"]["tables"]["datasets"]:
        row["end_date"] = "2024-01-05"
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, CAP, read, 100)


def maximum_source(index):
    import hashlib
    from dataclasses import replace

    from packages.rdagent_adapter.declared_upload import MAX_FILES
    from packages.strategy_sdk import canonical_json

    value = source(index)
    manifest = json.loads(value.manifest_bytes)
    artifacts = dict(value.artifacts)
    for i in range(MAX_FILES - len(artifacts)):
        name = f"extra-{i}.json"
        artifacts[name] = b"{}"
        manifest["artifact_files"][name] = hashlib.sha256(b"{}").hexdigest()
    assert len(artifacts) == MAX_FILES
    manifest.pop("content_hash")
    manifest["content_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    raw = canonical_json(manifest)
    return replace(
        value,
        manifest_bytes=raw,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        artifacts=artifacts,
    )


def test_two_maximum_original_inventories_restore_exact_260_artifacts():
    from packages.rdagent_adapter.declared_upload import MAX_BASKET_FILES

    snapshot, _, read = retained_sources((maximum_source(0), maximum_source(1)))
    target = snapshot["declared_upload_closure"]["tables"]["datasets"][-1]
    assert len(target["manifest"]["artifact_files"]) == MAX_BASKET_FILES == 260
    validate_snapshot(snapshot, CAP, read, 100)


def test_over_composed_inventory_bound_is_rejected_before_any_read():
    from backend.app.declared_upload_backup import retained_artifacts
    from packages.rdagent_adapter.declared_upload import MAX_BASKET_FILES

    manifest = {
        "schema_version": 3,
        "profile": PROFILE,
        "artifact_files": {
            f"file-{i}.json": "a" * 64 for i in range(MAX_BASKET_FILES + 1)
        },
    }
    with pytest.raises(ValueError):
        retained_artifacts(
            manifest,
            "datasets/owner/id",
            lambda *_: pytest.fail("oversized read"),
            declared_upload=True,
        )
