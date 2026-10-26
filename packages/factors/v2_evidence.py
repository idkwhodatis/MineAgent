"""DISABLED isolated-prefix evidence DTOs; no publication or verification authority.

These declarations are not LocalVerification and cannot enter the v1 provider.
Closure is necessary, not sufficient: future fenced DB/process/storage admission
must authenticate owner, current attempt, image/profile, stopped processes,
extraction, actual bytes, host row assembly and complete expected cutoffs. Even a
fully self-consistent forged graph is not authority. No native parser, archive
codec, legacy fallback, registration or route is provided here.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import (
    Contract,
    Count,
    Sha256,
    SnapshotInput,
    digest,
    imported_verification,
)
from packages.rdagent_adapter.assembly import CutoffRowAssembly
from packages.rdagent_adapter.contracts import RuntimePins
from packages.rdagent_adapter.prefix import PrefixManifest


class EvidenceObject(Contract):
    """Physical kind and semantic role are both hashed, never suffix-inferred."""

    role: Literal["source", "native_hdf", "normalized_values", "stitched_values"]
    object_id: UUID
    object_kind: Literal["code", "native_hdf", "values_partition"]
    media_type: Literal[
        "text/plain", "application/x-hdf5", "application/vnd.apache.parquet"
    ]
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=1, le=268_435_456)

    @model_validator(mode="after")
    def typed(self):
        expected = {
            "source": ("code", "text/plain"),
            "native_hdf": ("native_hdf", "application/x-hdf5"),
            "normalized_values": ("values_partition", "application/vnd.apache.parquet"),
            "stitched_values": ("values_partition", "application/vnd.apache.parquet"),
        }
        if (self.object_kind, self.media_type) != expected[self.role]:
            raise ValueError("Evidence role/kind/media mismatch")
        if self.role == "source" and self.size_bytes > 131_072:
            raise ValueError("Source byte budget exceeded")
        if self.role == "native_hdf" and self.size_bytes > 16_777_216:
            raise ValueError("Native HDF byte budget exceeded")
        return self


class SourceBinding(Contract):
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    generation_start_id: UUID
    generation_source_id: UUID
    evaluation_id: UUID
    factor_version_id: UUID
    source_sha256: Sha256
    snapshot_input_sha256: Sha256


class ProcessEvidence(Contract):
    """References to future authenticated process records, not stop attestations."""

    admission_id: UUID
    cutoff_execution_id: UUID
    stop_receipt_sha256: Sha256
    extraction_receipt_sha256: Sha256
    image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    profile: str = Field(min_length=1, max_length=200)
    binding: SourceBinding
    prefix_sha256: Sha256
    input_sha256: Sha256
    output: EvidenceObject


class CutoffEvidence(Contract):
    cutoff_execution_id: UUID
    cutoff_ordinal: int = Field(strict=True, ge=0, le=4095)
    prefix: PrefixManifest
    candidate: ProcessEvidence
    normalizer: ProcessEvidence
    assembly: CutoffRowAssembly

    @model_validator(mode="after")
    def linked(self):
        prefix_hash = digest(self.prefix)
        candidate, normalizer = self.candidate, self.normalizer
        if candidate.binding != normalizer.binding:
            raise ValueError("Cutoff process source differs")
        if (
            candidate.cutoff_execution_id != self.cutoff_execution_id
            or normalizer.cutoff_execution_id != self.cutoff_execution_id
            or candidate.binding.snapshot_input_sha256
            != self.prefix.snapshot_input_sha256
            or candidate.prefix_sha256 != prefix_hash
            or normalizer.prefix_sha256 != prefix_hash
            or candidate.input_sha256 != prefix_hash
            or normalizer.input_sha256 != candidate.output.sha256
            or candidate.output.size_bytes > self.prefix.max_output_bytes
            or candidate.output.role != "native_hdf"
            or normalizer.output.role != "normalized_values"
            or candidate.image != self.prefix.candidate_image
            or normalizer.image != self.prefix.normalizer_image
            or candidate.profile != self.prefix.candidate_profile
            or normalizer.profile != self.prefix.normalizer_profile
            or candidate.admission_id == normalizer.admission_id
        ):
            raise ValueError("Cutoff process/input/output closure differs")
        if (
            self.assembly.snapshot_input_sha256 != self.prefix.snapshot_input_sha256
            or self.assembly.projection_sha256 != prefix_hash
            or self.assembly.hdf_sha256 != candidate.output.sha256
            or self.assembly.normalized_values_sha256 != normalizer.output.sha256
            or self.assembly.row_count != len(self.prefix.listings)
            or self.assembly.valid_count > self.assembly.row_count
        ):
            raise ValueError("Host row assembly closure differs")
        return self


class PrefixEvidenceV2(Contract):
    schema_version: Literal["mineagent.isolated-prefix-evidence.v2"]
    binding: SourceBinding
    source: EvidenceObject
    cutoffs: tuple[CutoffEvidence, ...] = Field(min_length=1, max_length=4096)
    stitched_values: EvidenceObject
    output_rows_sha256: Sha256
    row_count: Count
    valid_count: Count
    missing_count: Count
    # Canonical digest of the ordered host assembly DTOs, not binary Parquet.
    row_assembly_sha256: Sha256
    inventory: tuple[EvidenceObject, ...] = Field(min_length=4, max_length=8194)

    @model_validator(mode="after")
    def closed(self):
        if (
            self.source.role != "source"
            or self.source.sha256 != self.binding.source_sha256
            or self.stitched_values.role != "stitched_values"
        ):
            raise ValueError("Source/final object role differs")
        keys = [
            (c.prefix.host_cutoff, c.prefix.output_event_time) for c in self.cutoffs
        ]
        admissions = [
            p.admission_id for c in self.cutoffs for p in (c.candidate, c.normalizer)
        ]
        if len(set(keys)) != len(keys) or keys != sorted(keys):
            raise ValueError("Cutoffs must be unique and ordered")
        if [c.cutoff_ordinal for c in self.cutoffs] != list(range(len(self.cutoffs))):
            raise ValueError("Cutoff ordinals must be contiguous")
        if len({c.cutoff_execution_id for c in self.cutoffs}) != len(self.cutoffs):
            raise ValueError("Cutoff execution identities must be unique")
        if len(set(admissions)) != len(admissions):
            raise ValueError("Fresh process admission required for every cutoff")
        if any(c.candidate.binding != self.binding for c in self.cutoffs):
            raise ValueError("Cutoff source/input differs")
        if (
            self.row_count != sum(c.assembly.row_count for c in self.cutoffs)
            or self.valid_count != sum(c.assembly.valid_count for c in self.cutoffs)
            or self.row_count != self.valid_count + self.missing_count
            or self.row_assembly_sha256
            != digest([c.assembly.model_dump(mode="json") for c in self.cutoffs])
        ):
            raise ValueError("Stitched row assembly/count closure differs")
        expected = (
            self.source,
            *(p.output for c in self.cutoffs for p in (c.candidate, c.normalizer)),
            self.stitched_values,
        )
        if self.inventory != expected:
            raise ValueError(
                "Inventory must retain exact ordered source/HDF/normalized/final objects"
            )
        if len({o.object_id for o in self.inventory}) != len(self.inventory):
            raise ValueError("Object identities must be unique")
        return self


class IsolatedPrefixDeclaration(Contract):
    """Exportable claim only. Never use this DTO to grant local verification."""

    schema_version: Literal["mineagent.isolated-prefix-declaration.v2"]
    kind: Literal["isolated_prefix_recomputed"]
    admission_profile: Literal["mineagent.isolated-prefix-recomputed.v2"]
    evidence_sha256: Sha256
    source_pit_verified: bool = Field(strict=True, default=False)

    @model_validator(mode="after")
    def no_source_pit(self):
        if self.source_pit_verified:
            raise ValueError("Isolated recomputation is not verified source PIT")
        return self


def validate_evidence_closure(
    evidence: PrefixEvidenceV2,
    *,
    expected_binding: SourceBinding,
    snapshot_input: SnapshotInput,
    expected_runtime: RuntimePins,
    expected_prefixes: tuple[PrefixManifest, ...],
    observed_objects: tuple[EvidenceObject, ...],
    expected_output_rows_sha256: str,
    declaration: IsolatedPrefixDeclaration | None = None,
) -> None:
    """Compare against independently supplied host expectations; grants nothing.

    observed_objects must eventually come from authenticated storage byte checks,
    not from a submitted manifest. This pure helper does not perform those checks.
    """
    evidence = PrefixEvidenceV2.model_validate(evidence)
    expected_binding = SourceBinding.model_validate(expected_binding)
    snapshot_input = SnapshotInput.model_validate(snapshot_input)
    expected_runtime = RuntimePins.model_validate(expected_runtime)
    if (
        snapshot_input.materialization_profile != "mineagent.rdagent-isolated-prefix.v2"
        or snapshot_input.adapter_version != expected_runtime.adapter_version
        or snapshot_input.environment_sha256 != digest(expected_runtime)
    ):
        raise ValueError("Snapshot materialization/runtime profile differs")
    if evidence.binding != expected_binding or (
        expected_binding.snapshot_input_sha256 != snapshot_input.sha256
        or expected_binding.source_sha256 != snapshot_input.definition.code_sha256
        or expected_binding.factor_version_id
        != snapshot_input.version.factor_version_id
    ):
        raise ValueError("Evidence differs from frozen source/input")
    for cutoff in evidence.cutoffs:
        prefix = cutoff.prefix
        if (
            prefix.candidate_image != expected_runtime.candidate_image
            or prefix.normalizer_image != expected_runtime.normalizer_image
            or prefix.candidate_profile != expected_runtime.candidate_profile
            or prefix.normalizer_profile != expected_runtime.normalizer_profile
            or prefix.calendar_sha256 != snapshot_input.calendar_sha256
            or prefix.market_timezone != snapshot_input.market_timezone
            or prefix.membership_sha256
            != snapshot_input.calculation_universe.membership_sha256
            or prefix.membership_available_at
            != snapshot_input.calculation_universe.membership_available_at
            or prefix.preprocessing != snapshot_input.definition.preprocessing
            or prefix.input_fields != snapshot_input.definition.input_fields
            or prefix.lookback_prior_sessions
            != snapshot_input.definition.lookback_sessions
            or prefix.lag_sessions != snapshot_input.definition.lag_sessions
            or tuple(x.listing_id for x in prefix.listings)
            != tuple(sorted(snapshot_input.calculation_universe.listing_ids))
            or not snapshot_input.window.start
            <= prefix.output_session
            < snapshot_input.window.end
        ):
            raise ValueError("Prefix differs from frozen snapshot semantics")
    if tuple(c.prefix for c in evidence.cutoffs) != expected_prefixes:
        raise ValueError("Expected cutoff/prefix inventory differs")
    if evidence.output_rows_sha256 != expected_output_rows_sha256:
        raise ValueError("Host stitched rows digest differs")
    if evidence.inventory != observed_objects:
        raise ValueError("Observed kind-qualified object inventory differs")
    if declaration is not None:
        declaration = IsolatedPrefixDeclaration.model_validate(declaration)
        if declaration.evidence_sha256 != digest(evidence):
            raise ValueError("Declaration evidence hash differs")


def imported_v2_verification(evidence: PrefixEvidenceV2):
    """Imports always lose isolated-recomputation claims, including valid graphs."""
    return imported_verification(
        evidence_sha256=digest(PrefixEvidenceV2.model_validate(evidence))
    )
