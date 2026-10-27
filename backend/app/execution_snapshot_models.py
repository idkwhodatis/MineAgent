"""Revision021 immutable execution snapshots, separate from legacy provider rows."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


class SnapshotScope:
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)


class ExecutionSnapshotPublication(SnapshotScope, Base):
    __tablename__ = "factor_execution_snapshot_publications"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest: Mapped[dict] = mapped_column(JSONB)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest_size_bytes: Mapped[int] = mapped_column(BigInteger)
    manifest_storage_key: Mapped[str] = mapped_column(String(300))
    journal: Mapped[dict] = mapped_column(JSONB)
    journal_sha256: Mapped[str] = mapped_column(String(64))
    journal_key: Mapped[str] = mapped_column(String(300))
    peak_bytes: Mapped[int] = mapped_column(BigInteger)
    peak_entries: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(*SCOPE, "id"),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("evaluation_id"),
        UniqueConstraint("reservation_id"),
        ForeignKeyConstraint(
            [*SCOPE, "evaluation_id", "start_id", "source_id", "factor_version_id"],
            [
                "factor_evaluation_starts." + x
                for x in (
                    *SCOPE,
                    "id",
                    "start_id",
                    "evaluated_source_id",
                    "factor_version_id",
                )
            ],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "reservation_id"],
            ["research_budget_reservations." + x for x in (*SCOPE, "id")],
        ),
        CheckConstraint(
            "generation>0 AND manifest_sha256 ~ '^[a-f0-9]{64}$' AND journal_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=1048576"
        ),
        CheckConstraint(
            "jsonb_typeof(journal)='object' AND octet_length(journal::text)<=2097152"
        ),
        CheckConstraint(
            "manifest_size_bytes BETWEEN 1 AND 1048576 AND peak_bytes>0 AND peak_entries BETWEEN 1 AND 256"
        ),
        CheckConstraint(
            "manifest_storage_key='factors/'||owner_id::text||'/objects/'||manifest_sha256||'.json'"
        ),
        CheckConstraint(
            "journal_key='factors/'||owner_id::text||'/objects/'||journal_sha256||'.json'"
        ),
    )


class ExecutionSnapshotPartition(SnapshotScope, Base):
    __tablename__ = "factor_execution_snapshot_partitions"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    cutoff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    native_publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    normalizer_admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    extraction_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_kind: Mapped[str] = mapped_column(String(30), default="stop")
    extraction_kind: Mapped[str] = mapped_column(String(30), default="extraction")
    binding: Mapped[dict] = mapped_column(JSONB)
    binding_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("normalizer_admission_id"),
        UniqueConstraint("cutoff_id"),
        ForeignKeyConstraint(
            [*SCOPE, "publication_id"],
            ["factor_execution_snapshot_publications." + x for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "cutoff_id"],
            ["factor_cutoff_executions." + x for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            ["native_publication_id", "normalizer_admission_id"],
            [
                "factor_native_normalizer_inputs.publication_id",
                "factor_native_normalizer_inputs.admission_id",
            ],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "normalizer_admission_id", "stop_receipt_id", "stop_kind"],
            [
                "research_process_receipts." + x
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        ForeignKeyConstraint(
            [
                *SCOPE,
                "normalizer_admission_id",
                "extraction_receipt_id",
                "extraction_kind",
            ],
            [
                "research_process_receipts." + x
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        CheckConstraint(
            "generation>0 AND ordinal BETWEEN 0 AND 63 AND stop_kind='stop' AND extraction_kind='extraction'"
        ),
        CheckConstraint(
            "jsonb_typeof(binding)='object' AND octet_length(binding::text)<=65536 AND binding_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class ExecutionSnapshotObject(Base):
    __tablename__ = "factor_execution_snapshot_objects"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "id", "object_kind", "sha256", "size_bytes", "storage_key"
        ),
        ForeignKeyConstraint(
            ["owner_id", "publication_id"],
            [
                "factor_execution_snapshot_publications.owner_id",
                "factor_execution_snapshot_publications.id",
            ],
        ),
        CheckConstraint(
            "object_kind IN ('normalized_values','assembled_rows','execution_manifest') AND sha256 ~ '^[a-f0-9]{64}$' AND size_bytes BETWEEN 1 AND 67108864"
        ),
        CheckConstraint(
            "size_bytes <= CASE WHEN object_kind='execution_manifest' THEN 1048576 ELSE 16777216 END"
        ),
        CheckConstraint(
            "storage_key='factors/'||owner_id::text||'/objects/'||sha256||CASE WHEN object_kind='normalized_values' THEN '.parquet' ELSE '.json' END"
        ),
    )


class ExecutionSnapshotOutcome(SnapshotScope, Base):
    __tablename__ = "factor_execution_snapshot_outcomes"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    output_sha256: Mapped[str] = mapped_column(String(64))
    physical_receipt_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "publication_id"),
        ForeignKeyConstraint(
            [*SCOPE, "publication_id"],
            ["factor_execution_snapshot_publications." + x for x in (*SCOPE, "id")],
        ),
        CheckConstraint(
            "generation>0 AND manifest_sha256 ~ '^[a-f0-9]{64}$' AND input_sha256 ~ '^[a-f0-9]{64}$' AND output_sha256 ~ '^[a-f0-9]{64}$' AND physical_receipt_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorExecutionEvaluationOutcome(SnapshotScope, Base):
    """Versioned valid result, never an alias for legacy feature_snapshots."""

    __tablename__ = "factor_execution_evaluation_outcomes"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation_start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    boundary_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24), default="valid")
    snapshot_input_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_output_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_manifest_sha256: Mapped[str] = mapped_column(String(64))
    feedback_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    feedback_object_kind: Mapped[str] = mapped_column(
        String(40), default="factor_generation_feedback"
    )
    feedback_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("evaluation_id"),
        UniqueConstraint("snapshot_id"),
        UniqueConstraint(*SCOPE, "id"),
        ForeignKeyConstraint(
            [*SCOPE, "evaluation_id"],
            ["factor_evaluation_starts." + x for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "boundary_id", "controller_launch_id", "generation_start_id"],
            [
                "factor_generation_boundaries." + x
                for x in (*SCOPE, "id", "controller_launch_id", "start_id")
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "snapshot_id"],
            [
                "factor_execution_snapshot_outcomes.owner_id",
                "factor_execution_snapshot_outcomes.publication_id",
            ],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "feedback_object_id",
                "feedback_object_kind",
                "feedback_sha256",
            ],
            [
                "research_objects." + x
                for x in ("owner_id", "id", "object_kind", "sha256")
            ],
        ),
        CheckConstraint(
            "generation>0 AND status='valid' AND snapshot_input_sha256 ~ '^[a-f0-9]{64}$' AND snapshot_output_sha256 ~ '^[a-f0-9]{64}$' AND snapshot_manifest_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "feedback_object_kind='factor_generation_feedback' AND feedback_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class ExecutionEvaluationClaim(SnapshotScope, Base):
    """Immediate cross-version result arbitration, not a successful outcome."""

    __tablename__ = "factor_evaluation_result_consumers"
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    consumer_kind: Mapped[str] = mapped_column(String(32))
    consumer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        ForeignKeyConstraint(
            [*SCOPE, "evaluation_id"],
            ["factor_evaluation_starts." + x for x in (*SCOPE, "id")],
        ),
        CheckConstraint(
            "generation>0 AND consumer_kind IN ('legacy_result','execution_result')"
        ),
    )
