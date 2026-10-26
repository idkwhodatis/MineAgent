"""DISABLED v2 storage declarations, not a filesystem/publication/recovery API.

No v1 path/parser/registry is extended. Actual integration requires a new explicit
capability/migration: factor_services.OBJECT_TYPES and physical_inventory (including
.h5 temporary names), FactorObject kind/media constraints, kind-qualified domain
FKs, research storage reservations and fenced publication/extraction. Update
factor_backup read_closure/validate closure and schema_capabilities, cold physical
backup/restore inventories, orphan/staging recovery and associated crash tests
atomically. Existing staging remains a recovery barrier, not disposable garbage.

DTOs and hashes never authenticate process stops, byte extraction, SQL commit,
current fences, reservation settlement, filesystem ownership or cleanup safety.
Journal declarations must be independently reconciled even when 'recorded'.
"""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, digest
from packages.factors.v2_evidence import EvidenceObject, PrefixEvidenceV2, SourceBinding

# A closed v2 mapping, deliberately NOT merged into the active v1 OBJECT_TYPES.
_SUFFIX = {"code": "py", "native_hdf": "h5", "values_partition": "parquet"}
MAX_INVENTORY_BYTES = 536_870_912
MAX_INVENTORY_OBJECTS = 8194


class StorageIdentityV2(Contract):
    schema_version: Literal["mineagent.factor-storage-identity.v2"]
    owner_id: UUID
    object: EvidenceObject
    storage_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def canonical(self):
        suffix = _SUFFIX[self.object.object_kind]
        expected = f"factors/{self.owner_id}/objects/{self.object.sha256}.{suffix}"
        if self.storage_key != expected:
            raise ValueError("Noncanonical v2 storage identity")
        return self


def storage_identity(owner_id: UUID, obj: EvidenceObject) -> StorageIdentityV2:
    """Construct a declaration; does not reserve or inspect its physical address."""
    obj = EvidenceObject.model_validate(obj)
    return StorageIdentityV2(
        schema_version="mineagent.factor-storage-identity.v2",
        owner_id=owner_id,
        object=obj,
        storage_key=f"factors/{owner_id}/objects/{obj.sha256}.{_SUFFIX[obj.object_kind]}",
    )


def validate_object_bytes(identity: StorageIdentityV2, content: bytes) -> None:
    """Bounded in-memory byte integrity only; no native HDF/Parquet interpretation."""
    identity = StorageIdentityV2.model_validate(identity)
    if type(content) is not bytes or len(content) != identity.object.size_bytes:
        raise ValueError("Object byte size/type differs")
    if hashlib.sha256(content).hexdigest() != identity.object.sha256:
        raise ValueError("Object bytes digest differs")


class PublicationInventoryV2(Contract):
    schema_version: Literal["mineagent.factor-publication-inventory.v2"]
    binding: SourceBinding
    evidence_sha256: Sha256
    objects: tuple[StorageIdentityV2, ...] = Field(
        min_length=4, max_length=MAX_INVENTORY_OBJECTS
    )
    total_bytes: int = Field(strict=True, ge=1, le=MAX_INVENTORY_BYTES)

    @model_validator(mode="after")
    def closed(self):
        if any(o.owner_id != self.binding.owner_id for o in self.objects):
            raise ValueError("Storage owner differs from evidence owner")
        if self.total_bytes != sum(o.object.size_bytes for o in self.objects):
            raise ValueError("Inventory byte total differs")
        if len({o.object.object_id for o in self.objects}) != len(self.objects):
            raise ValueError("Repeated object registration")
        # Same bytes may legitimately serve two roles, but cannot disguise a
        # conflicting registration at one physical key. This bounded profile
        # requires distinct retained addresses; alias support is not implicit.
        if len({o.storage_key for o in self.objects}) != len(self.objects):
            raise ValueError("Ambiguous shared physical address")
        return self


def validate_publication_inventory(
    inventory: PublicationInventoryV2, evidence: PrefixEvidenceV2
) -> None:
    inventory = PublicationInventoryV2.model_validate(inventory)
    evidence = PrefixEvidenceV2.model_validate(evidence)
    if (
        inventory.binding != evidence.binding
        or inventory.evidence_sha256 != digest(evidence)
        or tuple(o.object for o in inventory.objects) != evidence.inventory
    ):
        raise ValueError("Publication differs from exact v2 evidence inventory")


class ExtractionInventoryV2(Contract):
    schema_version: Literal["mineagent.factor-extraction-inventory.v2"]
    binding: SourceBinding
    evidence_sha256: Sha256
    cutoff_execution_id: UUID
    cutoff_ordinal: int = Field(strict=True, ge=0, le=4095)
    process_role: Literal["candidate", "normalizer"]
    admission_id: UUID
    prefix_sha256: Sha256
    stop_receipt_sha256: Sha256
    extraction_receipt_sha256: Sha256
    output: StorageIdentityV2

    @model_validator(mode="after")
    def role(self):
        expected = (
            "native_hdf" if self.process_role == "candidate" else "normalized_values"
        )
        if (
            self.output.object.role != expected
            or self.output.owner_id != self.binding.owner_id
        ):
            raise ValueError("Extraction kind/owner differs")
        return self


def validate_extraction_inventory(
    inventory: ExtractionInventoryV2, evidence: PrefixEvidenceV2
) -> None:
    inventory = ExtractionInventoryV2.model_validate(inventory)
    evidence = PrefixEvidenceV2.model_validate(evidence)
    if (
        inventory.binding != evidence.binding
        or inventory.evidence_sha256 != digest(evidence)
        or inventory.cutoff_ordinal >= len(evidence.cutoffs)
    ):
        raise ValueError("Extraction source/evidence/cutoff differs")
    cutoff = evidence.cutoffs[inventory.cutoff_ordinal]
    process = (
        cutoff.candidate if inventory.process_role == "candidate" else cutoff.normalizer
    )
    if (
        inventory.cutoff_execution_id != cutoff.cutoff_execution_id
        or inventory.admission_id != process.admission_id
        or inventory.prefix_sha256 != process.prefix_sha256
        or inventory.stop_receipt_sha256 != process.stop_receipt_sha256
        or inventory.extraction_receipt_sha256 != process.extraction_receipt_sha256
        or inventory.output.object != process.output
    ):
        raise ValueError("Extraction process/output closure differs")


class TemporaryObjectV2(Contract):
    final: StorageIdentityV2
    nonce: str = Field(pattern=r"^[a-f0-9]{32}$")
    temporary_key: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def canonical(self):
        directory, name = self.final.storage_key.rsplit("/", 1)
        if self.temporary_key != f"{directory}/.{name}.{self.nonce}.tmp":
            raise ValueError("Noncanonical temporary object identity")
        return self


class PublicationJournalV2(Contract):
    """Bounded recovery intent/observation, never a cleanup or commit certificate.

    All declared objects stay conservatively charged until independent physical,
    process, transaction and fence reconciliation. No state permits deletion.
    """

    schema_version: Literal["mineagent.factor-publication-journal.v2"]
    journal_id: UUID
    inventory: PublicationInventoryV2
    journal_key: str = Field(min_length=1, max_length=200)
    reservation_id: UUID
    temporary_objects: tuple[TemporaryObjectV2, ...] = Field(
        min_length=4, max_length=MAX_INVENTORY_OBJECTS
    )
    observed_state: Literal["pending", "uncertain", "recorded"]
    publication_receipt_sha256: Sha256 | None = None
    # Reservation covers all retained final plus temporary object bytes; actual
    # journal/process/workspace costs are additionally charged by future host.
    declared_object_peak_bytes: int = Field(
        strict=True, ge=1, le=2 * MAX_INVENTORY_BYTES
    )

    @model_validator(mode="after")
    def closed(self):
        owner = self.inventory.binding.owner_id
        expected_key = f"factors/{owner}/staging/{self.journal_id}/publication.v2.json"
        if self.journal_key != expected_key:
            raise ValueError("Noncanonical v2 journal key")
        if tuple(t.final for t in self.temporary_objects) != self.inventory.objects:
            raise ValueError("Temporary inventory differs from publication")
        if len({t.nonce for t in self.temporary_objects}) != len(
            self.temporary_objects
        ):
            raise ValueError("Temporary write nonces must be distinct")
        if self.declared_object_peak_bytes != 2 * self.inventory.total_bytes:
            raise ValueError("Temporary and final bytes must both remain charged")
        if (self.observed_state == "recorded") != (
            self.publication_receipt_sha256 is not None
        ):
            raise ValueError("Publication observation/receipt mismatch")
        return self
