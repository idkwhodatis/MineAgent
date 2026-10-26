import hashlib
from uuid import UUID

import pytest

from packages.factors.contracts import digest
from packages.factors.v2_storage_contracts import (
    ExtractionInventoryV2,
    PublicationInventoryV2,
    PublicationJournalV2,
    StorageIdentityV2,
    TemporaryObjectV2,
    storage_identity,
    validate_extraction_inventory,
    validate_object_bytes,
    validate_publication_inventory,
)
from tests.factors.test_v2_evidence import graph


def fixture():
    _, evidence = graph()
    objects = tuple(
        storage_identity(evidence.binding.owner_id, o) for o in evidence.inventory
    )
    inventory = PublicationInventoryV2(
        schema_version="mineagent.factor-publication-inventory.v2",
        binding=evidence.binding,
        evidence_sha256=digest(evidence),
        objects=objects,
        total_bytes=sum(o.object.size_bytes for o in objects),
    )
    temporary = tuple(
        TemporaryObjectV2(
            final=o,
            nonce=f"{n:032x}",
            temporary_key=o.storage_key.rsplit("/", 1)[0]
            + "/."
            + o.storage_key.rsplit("/", 1)[1]
            + f".{n:032x}.tmp",
        )
        for n, o in enumerate(objects, 1)
    )
    journal_id = UUID(int=88)
    journal = PublicationJournalV2(
        schema_version="mineagent.factor-publication-journal.v2",
        journal_id=journal_id,
        inventory=inventory,
        journal_key=f"factors/{evidence.binding.owner_id}/staging/{journal_id}/publication.v2.json",
        reservation_id=UUID(int=89),
        temporary_objects=temporary,
        observed_state="uncertain",
        declared_object_peak_bytes=2 * inventory.total_bytes,
    )
    return evidence, inventory, journal


def extraction(evidence, inventory, role):
    cutoff = evidence.cutoffs[0]
    process = cutoff.candidate if role == "candidate" else cutoff.normalizer
    return ExtractionInventoryV2(
        schema_version="mineagent.factor-extraction-inventory.v2",
        binding=evidence.binding,
        evidence_sha256=digest(evidence),
        cutoff_execution_id=cutoff.cutoff_execution_id,
        cutoff_ordinal=0,
        process_role=role,
        admission_id=process.admission_id,
        prefix_sha256=process.prefix_sha256,
        stop_receipt_sha256=process.stop_receipt_sha256,
        extraction_receipt_sha256=process.extraction_receipt_sha256,
        output=inventory.objects[1 if role == "candidate" else 2],
    )


def test_versioned_roundtrip_and_exact_extraction_closure():
    evidence, inventory, journal = fixture()
    validate_publication_inventory(inventory, evidence)
    assert (
        PublicationJournalV2.model_validate_json(journal.model_dump_json()) == journal
    )
    for role in ("candidate", "normalizer"):
        validate_extraction_inventory(extraction(evidence, inventory, role), evidence)
    assert inventory.objects[1].storage_key.endswith(".h5")
    assert inventory.objects[1].object.media_type == "application/x-hdf5"
    assert journal.observed_state == "uncertain"  # no cleanup/settlement authority


@pytest.mark.parametrize(
    "key",
    [
        "../x",
        "/absolute",
        "factors/../objects/x.h5",
        "x\\y",
        "x%2fy",
        "x\x00",
        "x.h5/",
        "objects/x.h5",
    ],
)
def test_path_traversal_and_noncanonical_keys(key):
    _, inventory, _ = fixture()
    raw = inventory.objects[1].model_dump(mode="json")
    raw["storage_key"] = key
    with pytest.raises(ValueError):
        StorageIdentityV2.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        "suffix",
        "hash",
        "kind",
        "media",
        "upper_hash",
        "owner",
        "v1",
        "bool_size",
        "zero_size",
        "over_cap",
    ],
)
def test_kind_hash_media_and_size_closed(change):
    _, inventory, _ = fixture()
    raw = inventory.objects[1].model_dump(mode="json")
    if change == "suffix":
        raw["storage_key"] = raw["storage_key"][:-2] + "json"
    elif change == "hash":
        raw["object"]["sha256"] = "d" * 64
    elif change == "kind":
        raw["object"]["object_kind"] = "native_evidence"
    elif change == "media":
        raw["object"]["media_type"] = "application/json"
    elif change == "upper_hash":
        raw["object"]["sha256"] = "A" * 64
    elif change == "owner":
        raw["owner_id"] = str(UUID(int=99))
    elif change == "v1":
        raw["schema_version"] = "mineagent.factor-storage-identity.v1"
    elif change == "bool_size":
        raw["object"]["size_bytes"] = True
    elif change == "zero_size":
        raw["object"]["size_bytes"] = 0
    else:
        raw["object"]["size_bytes"] = 16_777_217
    with pytest.raises(ValueError):
        StorageIdentityV2.model_validate(raw)


@pytest.mark.parametrize(
    "change", ["missing", "sum", "cap", "binding", "hash", "reorder"]
)
def test_publication_inventory_closed(change):
    evidence, inventory, _ = fixture()
    raw = inventory.model_dump(mode="json")
    if change == "missing":
        raw["objects"].pop()
    elif change == "sum":
        raw["total_bytes"] += 1
    elif change == "cap":
        raw["total_bytes"] = 536_870_913
    elif change == "binding":
        raw["binding"]["generation"] += 1
    elif change == "hash":
        raw["evidence_sha256"] = "0" * 64
    else:
        raw["objects"].reverse()
    with pytest.raises(ValueError):
        validate_publication_inventory(
            PublicationInventoryV2.model_validate(raw), evidence
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("cutoff_execution_id", str(UUID(int=99))),
        ("cutoff_ordinal", 1),
        ("admission_id", str(UUID(int=99))),
        ("stop_receipt_sha256", "0" * 64),
        ("extraction_receipt_sha256", "0" * 64),
        ("prefix_sha256", "0" * 64),
        ("process_role", "normalizer"),
    ],
)
def test_extraction_substitution(field, value):
    evidence, inventory, _ = fixture()
    raw = extraction(evidence, inventory, "candidate").model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        validate_extraction_inventory(
            ExtractionInventoryV2.model_validate(raw), evidence
        )


@pytest.mark.parametrize(
    "change",
    [
        "path",
        "temporary",
        "nonce",
        "missing",
        "undercharge",
        "receipt",
        "recorded",
        "legacy",
    ],
)
def test_journal_is_exact_not_cleanup_permission(change):
    _, _, journal = fixture()
    raw = journal.model_dump(mode="json")
    if change == "path":
        raw["journal_key"] = "../journal.json"
    elif change == "temporary":
        raw["temporary_objects"][0]["temporary_key"] = raw["temporary_objects"][0][
            "final"
        ]["storage_key"]
    elif change == "nonce":
        raw["temporary_objects"][0]["nonce"] = "A" * 32
    elif change == "missing":
        raw["temporary_objects"].pop()
    elif change == "undercharge":
        raw["declared_object_peak_bytes"] //= 2
    elif change == "receipt":
        raw["publication_receipt_sha256"] = "a" * 64
    elif change == "recorded":
        raw["observed_state"] = "recorded"
    else:
        raw["schema_version"] = "mineagent.rdagent-source-publication.v1"
    with pytest.raises(ValueError):
        PublicationJournalV2.model_validate(raw)


def test_byte_hashing_is_bounded_not_native_parsing():
    _, inventory, _ = fixture()
    raw = inventory.objects[1].model_dump(mode="json")
    content = b"not actually an HDF file"
    sha = hashlib.sha256(content).hexdigest()
    raw["object"].update(sha256=sha, size_bytes=len(content))
    raw["storage_key"] = f"factors/{raw['owner_id']}/objects/{sha}.h5"
    identity = StorageIdentityV2.model_validate(raw)
    validate_object_bytes(
        identity, content
    )  # integrity, explicitly NOT format/admission proof
    for bad in (b"x" * len(content), content + b"x", bytearray(content)):
        with pytest.raises(ValueError):
            validate_object_bytes(identity, bad)
