"""Pre-normalizer HDF publication declaration; never self-authenticating authority.

V1 remains disabled with its staging recovery-barrier semantics. V2 has neither
an evaluation nor a completed aggregate dependency and uses dedicated journals,
never masquerading as a stopped parser. Only fresh persisted receipt/source
resolution can authorize its use. HDF content stays opaque on the host.
"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import model_validator

from packages.factors.contracts import Contract, Sha256
from packages.factors.v2_storage_contracts import StorageIdentityV2
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)


class NativePublicationIntent(Contract):
    schema_version: Literal["mineagent.candidate-native-intent.v2"]
    journal_id: UUID
    reservation_id: UUID
    admission: ProcessDeclarationV2
    source_id: UUID
    generation_start_id: UUID
    factor_version_id: UUID
    source_object_id: UUID
    source_sha256: Sha256
    prefix_sha256: Sha256
    snapshot_input_sha256: Sha256
    cutoff: datetime
    stop_receipt_id: UUID
    stop_receipt_sha256: Sha256
    extraction_receipt_id: UUID
    extraction_receipt_sha256: Sha256
    output: StorageIdentityV2
    journal_key: str

    @model_validator(mode="after")
    def closed(self):
        if self.admission.role != "factor_cutoff_candidate" or not isinstance(
            self.admission.ipc, NoIPC
        ):
            raise ValueError("Candidate no-IPC admission required")
        if (
            self.output.object.object_kind != "native_hdf"
            or self.output.object.role != "native_hdf"
        ):
            raise ValueError("Candidate intent requires opaque native_hdf")
        if self.output.owner_id != self.admission.owner_id:
            raise ValueError("Candidate owner differs")
        if self.reservation_id == self.admission.reservation_id:
            raise ValueError("Storage/process reservations must differ")
        if self.stop_receipt_id == self.extraction_receipt_id:
            raise ValueError("Stop/extraction receipts must differ")
        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() != timedelta(0):
            raise ValueError("UTC cutoff required")
        if (
            self.journal_key
            != f"factors/{self.output.owner_id}/native-journals/{self.journal_id}.json"
        ):
            raise ValueError("Noncanonical native publication journal")
        return self
