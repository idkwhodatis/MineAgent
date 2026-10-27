"""024 protected launch and retained checkpoint upload obligations.

All rows are immutable. Chunk payloads are retained research objects, never
unaccounted database byte blobs; these rows only bind exact ordered identities.
No import registers a public issuer or enables a process profile.
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.openevolve_models import SCOPE, AttemptEvidence, fk, scope_constraints


def epoch_fk():
    return fk([*SCOPE, "epoch_id"], "strategy_search_epochs", [*SCOPE, "id"])


class SearchControllerLaunch(AttemptEvidence, Base):
    __tablename__ = "search_controller_launches"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bootstrap: Mapped[dict] = mapped_column(JSONB)
    bootstrap_canonical: Mapped[str] = mapped_column(Text)
    bootstrap_sha256: Mapped[str] = mapped_column(String(64))
    input_inventory_sha256: Mapped[str] = mapped_column(String(64))
    physical_profile_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("epoch_id"),
        UniqueConstraint("admission_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "id", "admission_id"),
        fk([*SCOPE, "admission_id"], "research_process_admissions", [*SCOPE, "id"]),
        CheckConstraint(
            "bootstrap_sha256 ~ '^[a-f0-9]{64}$' AND input_inventory_sha256 ~ '^[a-f0-9]{64}$' AND physical_profile_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(bootstrap)='object' AND octet_length(bootstrap::text)<=262144"
        ),
        CheckConstraint(
            "octet_length(bootstrap_canonical) BETWEEN 2 AND 262144 AND bootstrap_canonical::jsonb=bootstrap AND encode(sha256(convert_to(bootstrap_canonical,'UTF8')),'hex')=bootstrap_sha256"
        ),
    )


class SearchCheckpointUpload(AttemptEvidence, Base):
    __tablename__ = "search_checkpoint_uploads"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    checkpoint_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inventory: Mapped[dict] = mapped_column(JSONB)
    inventory_sha256: Mapped[str] = mapped_column(String(64))
    total_bytes: Mapped[int] = mapped_column(Integer)
    file_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("checkpoint_intent_id"),
        UniqueConstraint("reservation_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "id"),
        fk(
            [*SCOPE, "checkpoint_intent_id"],
            "search_checkpoint_intents",
            [*SCOPE, "id"],
        ),
        fk([*SCOPE, "reservation_id"], "research_budget_reservations", [*SCOPE, "id"]),
        CheckConstraint(
            "inventory_sha256 ~ '^[a-f0-9]{64}$' AND jsonb_typeof(inventory)='object' AND octet_length(inventory::text)<=8192"
        ),
        CheckConstraint(
            "total_bytes BETWEEN 4 AND 8388608 AND file_count BETWEEN 4 AND 11 AND chunk_count BETWEEN 4 AND 88"
        ),
    )


class SearchCheckpointChunk(AttemptEvidence, Base):
    __tablename__ = "search_checkpoint_chunks"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upload_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(100))
    offset_bytes: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_sha256: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(String(40))
    object_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("upload_id", "sequence"),
        UniqueConstraint("upload_id", "file_name", "offset_bytes"),
        fk(
            [*SCOPE, "epoch_id", "upload_id"],
            "search_checkpoint_uploads",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            ["owner_id", "object_id", "object_kind", "object_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "object_kind='search_checkpoint_chunk' AND object_sha256 ~ '^[a-f0-9]{64}$' AND chunk_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "sequence BETWEEN 0 AND 87 AND offset_bytes BETWEEN 0 AND 524287 AND offset_bytes%65536=0 AND size_bytes BETWEEN 1 AND 65536"
        ),
        CheckConstraint(
            "file_name ~ '^(metadata.json|best_program.py|best_program_info.json|programs/[a-f0-9-]{36}.json)$'"
        ),
    )


class SearchCheckpointUploadCompletion(AttemptEvidence, Base):
    __tablename__ = "search_checkpoint_upload_completions"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upload_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inventory_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inventory_kind: Mapped[str] = mapped_column(String(40))
    inventory_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("upload_id"),
        fk(
            [*SCOPE, "epoch_id", "upload_id"],
            "search_checkpoint_uploads",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            ["owner_id", "inventory_object_id", "inventory_kind", "inventory_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "inventory_kind='search_checkpoint_inventory' AND inventory_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class SearchEvaluationInput(AttemptEvidence, Base):
    __tablename__ = "search_evaluation_inputs"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest_kind: Mapped[str] = mapped_column(String(40))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("evaluation_id"),
        fk([*SCOPE, "evaluation_id"], "search_evaluations", [*SCOPE, "id"]),
        fk(
            ["owner_id", "manifest_object_id", "manifest_kind", "manifest_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "manifest_kind='search_evaluation_input' AND manifest_sha256 ~ '^[a-f0-9]{64}$' AND manifest_size_bytes BETWEEN 1 AND 65536"
        ),
    )


class SearchEvaluationPreparation(AttemptEvidence, Base):
    """One reserved physical evaluation, before any source/dataset staging read.

    Its id is the future SearchEvaluation id. Pending rows are durable uncertain
    obligations, not an execution grant; admission must consume this exact tuple.
    """

    __tablename__ = "search_evaluation_preparations"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    window_id: Mapped[str] = mapped_column(String(48))
    execution_sha256: Mapped[str] = mapped_column(String(64))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    physical_ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = scope_constraints() + (
        epoch_fk(),
        UniqueConstraint("epoch_id", "candidate_id", "window_id"),
        UniqueConstraint("admission_id"),
        UniqueConstraint("reservation_id"),
        UniqueConstraint("logical_ticket_id"),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        fk([*SCOPE, "reservation_id"], "research_budget_reservations", [*SCOPE, "id"]),
        CheckConstraint(
            "physical_ordinal=1 AND source_sha256 ~ '^[0-9a-f]{64}$' AND execution_sha256 ~ '^[0-9a-f]{64}$' AND window_id ~ '^[a-z][a-z0-9_-]{0,47}$'"
        ),
    )
