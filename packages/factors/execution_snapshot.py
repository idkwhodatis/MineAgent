"""Versioned actual-execution snapshot commitments, never detached authority.

Separate from disabled v2_evidence: numeric input identities bind the complete
persisted inventory, not just a prefix or HDF hash. No legacy verification kind,
provider admission, native parser, or final generation outcome is implied.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, SnapshotInput

MAX_CUTOFFS = 64
MAX_SNAPSHOT_ROWS = 100_000
MAX_SNAPSHOT_BYTES = 67_108_864


class ExecutionObject(Contract):
    object_id: UUID
    owner_id: UUID
    kind: Literal["normalized_values", "assembled_rows", "execution_manifest"]
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=1, le=MAX_SNAPSHOT_BYTES)
    storage_key: str

    @model_validator(mode="after")
    def canonical(self):
        maximum = {
            "normalized_values": 16_777_216,
            "assembled_rows": 16_777_216,
            "execution_manifest": 1_048_576,
        }[self.kind]
        if self.size_bytes > maximum:
            raise ValueError("Execution object kind byte bound exceeded")
        suffix = "parquet" if self.kind == "normalized_values" else "json"
        if (
            self.storage_key
            != f"factors/{self.owner_id}/objects/{self.sha256}.{suffix}"
        ):
            raise ValueError("Execution object address differs")
        return self


class NormalizedPartition(Contract):
    cutoff_id: UUID
    ordinal: int = Field(strict=True, ge=0, lt=MAX_CUTOFFS)
    prefix_sha256: Sha256
    native_publication_id: UUID
    candidate_admission_id: UUID
    normalizer_admission_id: UUID
    normalizer_input_sha256: Sha256
    stop_receipt_id: UUID
    stop_receipt_sha256: Sha256
    extraction_receipt_id: UUID
    extraction_receipt_sha256: Sha256
    primitive_report_sha256: Sha256
    primitive_report_size_bytes: int = Field(strict=True, ge=1, le=4_194_304)
    values: ExecutionObject
    assembly_sha256: Sha256
    output_rows_sha256: Sha256
    row_count: int = Field(strict=True, ge=1, le=10_000)
    valid_count: int = Field(strict=True, ge=0, le=10_000)

    @model_validator(mode="after")
    def distinct(self):
        if self.values.kind != "normalized_values" or self.valid_count > self.row_count:
            raise ValueError("Normalized partition kind/count differs")
        if self.candidate_admission_id == self.normalizer_admission_id:
            raise ValueError("Candidate and normalizer must be distinct units")
        if self.stop_receipt_id == self.extraction_receipt_id:
            raise ValueError("Stop and extraction receipts must be distinct")
        return self


class ExecutionSnapshotManifest(Contract):
    schema_version: Literal["mineagent.factor-execution-snapshot.v1"]
    snapshot_id: UUID
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    generation_start_id: UUID
    source_id: UUID
    evaluation_id: UUID
    source_read_witness_id: UUID
    source_read_witness_sha256: Sha256
    input: SnapshotInput
    input_sha256: Sha256
    expected_cutoffs: int = Field(strict=True, ge=1, le=MAX_CUTOFFS)
    partitions: tuple[NormalizedPartition, ...] = Field(
        min_length=1, max_length=MAX_CUTOFFS
    )
    assembled_rows: ExecutionObject
    output_rows_sha256: Sha256
    row_count: int = Field(strict=True, ge=1, le=MAX_SNAPSHOT_ROWS)
    valid_count: int = Field(strict=True, ge=0, le=MAX_SNAPSHOT_ROWS)
    dependency_claim: Literal["provided_not_proven_read"] = "provided_not_proven_read"

    @model_validator(mode="after")
    def closed(self):
        if self.input.sha256 != self.input_sha256:
            raise ValueError("Snapshot input commitment differs")
        if self.expected_cutoffs != len(self.partitions) or tuple(
            p.ordinal for p in self.partitions
        ) != tuple(range(self.expected_cutoffs)):
            raise ValueError("Snapshot cutoff inventory is incomplete or unordered")
        for field in (
            "cutoff_id",
            "native_publication_id",
            "candidate_admission_id",
            "normalizer_admission_id",
            "stop_receipt_id",
            "extraction_receipt_id",
        ):
            if len({getattr(p, field) for p in self.partitions}) != len(
                self.partitions
            ):
                raise ValueError("Snapshot repeats execution evidence")
        if self.assembled_rows.kind != "assembled_rows" or any(
            o.owner_id != self.owner_id
            for o in (self.assembled_rows, *(p.values for p in self.partitions))
        ):
            raise ValueError("Snapshot object kind/owner differs")
        if self.row_count != sum(
            p.row_count for p in self.partitions
        ) or self.valid_count != sum(p.valid_count for p in self.partitions):
            raise ValueError("Snapshot row counts do not close")
        ids = [
            self.assembled_rows.object_id,
            *(p.values.object_id for p in self.partitions),
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Snapshot repeats object identity")
        # Identical partitions may share physical bytes, but registration IDs and
        # exact per-cutoff process bindings cannot be aliased.
        return self


class ExecutionPublicationJournal(Contract):
    schema_version: Literal["mineagent.execution-snapshot-publication.v1"]
    publication_id: UUID
    reservation_id: UUID
    manifest: ExecutionSnapshotManifest
    manifest_object: ExecutionObject
    peak_bytes: int = Field(strict=True, ge=1)
    peak_entries: int = Field(strict=True, ge=1, le=256)

    @model_validator(mode="after")
    def closed(self):
        from packages.factors.contracts import canonical_bytes, digest

        objects = (
            self.manifest_object,
            self.manifest.assembled_rows,
            *(p.values for p in self.manifest.partitions),
        )
        total = sum(obj.size_bytes for obj in objects)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot physical byte budget exceeded")
        if (
            self.manifest_object.owner_id != self.manifest.owner_id
            or self.manifest_object.kind != "execution_manifest"
            or self.manifest_object.sha256 != digest(self.manifest)
            or self.manifest_object.size_bytes != len(canonical_bytes(self.manifest))
            or self.publication_id != self.manifest.snapshot_id
        ):
            raise ValueError("Snapshot manifest object differs")
        if (
            self.peak_bytes != 2 * total + 4_194_304
            or self.peak_entries != 2 * (len(objects) + 1) + 3
        ):
            raise ValueError("Snapshot pending peak differs")
        if len({o.object_id for o in objects}) != len(objects):
            raise ValueError("Snapshot manifest repeats object identity")
        if self.manifest_object.size_bytes > 1_048_576:
            raise ValueError("Snapshot manifest metadata bound exceeded")
        return self
