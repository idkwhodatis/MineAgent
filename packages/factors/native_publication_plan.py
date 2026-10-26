"""Disabled, pre-normalizer candidate HDF intent; NOT publication authority.

Unlike a completed PrefixEvidenceV2 bundle this declaration has no normalizer or
stitched snapshot dependency. A future trusted host must independently resolve
all persisted owner/fence/admission/stop/extraction/reservation references before
writing, and again before registering bytes in a fresh transaction. No issuer,
writer, cleanup, budget settlement, native parser or active schema is provided.
"""

from typing import Literal
from uuid import UUID

from pydantic import model_validator

from packages.factors.contracts import Contract, Sha256, digest
from packages.factors.v2_evidence import ProcessEvidence
from packages.factors.v2_storage_contracts import StorageIdentityV2, TemporaryObjectV2
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)


class CandidateNativeIntent(Contract):
    schema_version: Literal["mineagent.candidate-native-intent.v1"]
    journal_id: UUID
    reservation_id: UUID
    process_role: Literal["factor_cutoff_candidate"]
    admission: ProcessDeclarationV2
    source_object_id: UUID
    stop_receipt_id: UUID
    extraction_receipt_id: UUID
    process: ProcessEvidence
    output: StorageIdentityV2
    temporary: TemporaryObjectV2
    journal_key: str
    # A declaration can never assert that a DB outcome committed or cleanup is safe.
    observed_state: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def same_closed_identity(self):
        if (
            self.output.object.object_kind != "native_hdf"
            or self.output.object.role != "native_hdf"
        ):
            raise ValueError("Candidate intent requires opaque native_hdf")
        if (
            self.process.output != self.output.object
            or self.output.owner_id != self.process.binding.owner_id
        ):
            raise ValueError("Candidate output/process owner identity differs")
        if self.temporary.final != self.output:
            raise ValueError("Candidate temporary identity differs")
        binding, admission = self.process.binding, self.admission
        if not isinstance(admission.ipc, NoIPC) or (
            admission.role,
            admission.id,
            admission.owner_id,
            admission.run_id,
            admission.attempt_id,
            admission.generation,
            admission.logical_operation_id,
            admission.profile,
            admission.image_id,
            admission.input_sha256,
        ) != (
            "factor_cutoff_candidate",
            self.process.admission_id,
            binding.owner_id,
            binding.run_id,
            binding.attempt_id,
            binding.generation,
            self.process.cutoff_execution_id,
            self.process.profile,
            self.process.image,
            self.process.input_sha256,
        ):
            raise ValueError("Candidate v2 admission differs from exact evidence scope")
        if self.reservation_id == admission.reservation_id:
            raise ValueError("Storage and process reservations must be distinct")
        if self.stop_receipt_id == self.extraction_receipt_id:
            raise ValueError("Stop and extraction receipts must be distinct")
        expected = f"factors/{self.output.owner_id}/staging/{self.journal_id}/candidate-native.v1.json"
        if self.journal_key != expected:
            raise ValueError("Noncanonical candidate journal identity")
        return self


class CandidateNativePublicationWitness(Contract):
    """Future persisted outcome references; never self-authenticating commit proof."""

    schema_version: Literal["mineagent.candidate-native-publication-witness.v1"]
    intent_sha256: Sha256
    publication_id: UUID
    outcome_id: UUID
    object: StorageIdentityV2
    journal_sha256: Sha256
    physical_receipt_sha256: Sha256


def validate_candidate_publication_references(intent, witness):
    """Relational/hash agreement only; caller must independently resolve DB rows.

    Success confers no stop, reservation, commit, extraction or cleanup authority.
    A later normalizer input witness must reread the registered immutable bytes,
    authenticate the persisted current-fence outcome and stopped candidate unit.
    """
    intent = CandidateNativeIntent.model_validate(intent)
    witness = CandidateNativePublicationWitness.model_validate(witness)
    if (
        witness.intent_sha256 != digest(intent)
        or witness.journal_sha256 != digest(intent)
        or witness.object != intent.output
    ):
        raise ValueError("Candidate publication witness differs from exact intent")
