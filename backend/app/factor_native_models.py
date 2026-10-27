"""Per-candidate immutable HDF publication; never a completed factor aggregate.

These rows are separate from historical FactorObject/native_evidence JSON. The
migration must install cross-family reservation arbitration and semantic triggers
before any service is usable. ORM declarations alone are not schema authority.
"""

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


class NativeHDFPublication(Base):
    __tablename__ = "factor_native_publications"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    cutoff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    extraction_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_kind: Mapped[str] = mapped_column(String(30), default="stop")
    extraction_kind: Mapped[str] = mapped_column(String(30), default="extraction")
    intent: Mapped[dict] = mapped_column(JSONB)
    intent_sha256: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    journal_key: Mapped[str] = mapped_column(String(300))
    peak_bytes: Mapped[int] = mapped_column(BigInteger)
    peak_entries: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(*SCOPE, "id"),
        UniqueConstraint(
            "owner_id", "id", "object_id", "sha256", "size_bytes", "storage_key"
        ),
        UniqueConstraint("reservation_id"),
        UniqueConstraint("admission_id"),
        UniqueConstraint("object_id"),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id"],
            [f"research_process_admissions.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "reservation_id"],
            [f"research_budget_reservations.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_object_id", "source_sha256"],
            [f"factor_objects.{x}" for x in ("owner_id", "id", "sha256")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "source_id"],
            [f"factor_generation_sources.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id", "stop_receipt_id", "stop_kind"],
            [
                f"research_process_receipts.{x}"
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id", "extraction_receipt_id", "extraction_kind"],
            [
                f"research_process_receipts.{x}"
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "cutoff_id", "admission_id", "source_id"],
            [
                f"factor_cutoff_executions.{x}"
                for x in (*SCOPE, "id", "candidate_admission_id", "evaluated_source_id")
            ],
        ),
        CheckConstraint("stop_kind = 'stop' AND extraction_kind = 'extraction'"),
        CheckConstraint("generation > 0 AND size_bytes BETWEEN 1 AND 16777216"),
        CheckConstraint(
            "sha256 ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(intent) = 'object' AND octet_length(intent::text) <= 131072"
        ),
        CheckConstraint("peak_bytes = 2 * size_bytes + 131072 AND peak_entries = 8"),
        CheckConstraint(
            "storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || '.h5'"
        ),
        CheckConstraint(
            "journal_key = 'factors/' || owner_id::text || '/native-journals/' || id::text || '.json'"
        ),
        CheckConstraint("stop_receipt_id <> extraction_receipt_id"),
    )


class NativeHDFObject(Base):
    __tablename__ = "factor_native_objects"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(String(40), default="native_hdf")
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(100), default="application/x-hdf5")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id", "sha256", "size_bytes", "storage_key"),
        CheckConstraint(
            "object_kind = 'native_hdf' AND media_type = 'application/x-hdf5'"
        ),
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 16777216 AND sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || '.h5'"
        ),
    )


class NativeHDFOutcome(Base):
    __tablename__ = "factor_native_outcomes"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    journal_sha256: Mapped[str] = mapped_column(String(64))
    physical_receipt_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "owner_id",
                "publication_id",
                "object_id",
                "sha256",
                "size_bytes",
                "storage_key",
            ],
            [
                f"factor_native_publications.{x}"
                for x in (
                    "owner_id",
                    "id",
                    "object_id",
                    "sha256",
                    "size_bytes",
                    "storage_key",
                )
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "object_id", "sha256", "size_bytes", "storage_key"],
            [
                f"factor_native_objects.{x}"
                for x in ("owner_id", "id", "sha256", "size_bytes", "storage_key")
            ],
        ),
        CheckConstraint(
            "journal_sha256 ~ '^[a-f0-9]{64}$' AND physical_receipt_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class NativeNormalizerInput(Base):
    """Exactly one normalizer admission consumes one completed candidate object."""

    __tablename__ = "factor_native_normalizer_inputs"
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    input_sha256: Mapped[str] = mapped_column(String(64))
    intent_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        # Revision021 exact snapshot partition FK; historical019 DDL stays fixed.
        UniqueConstraint(
            "publication_id", "admission_id", name="uq_native_normalizer_snapshot_pair"
        ),
        UniqueConstraint("publication_id"),
        UniqueConstraint("reservation_id"),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id"],
            [f"research_process_admissions.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "reservation_id"],
            [f"research_budget_reservations.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "publication_id"],
            [f"factor_native_publications.{x}" for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "publication_id",
                "object_id",
                "sha256",
                "size_bytes",
                "storage_key",
            ],
            [
                f"factor_native_publications.{x}"
                for x in (
                    "owner_id",
                    "id",
                    "object_id",
                    "sha256",
                    "size_bytes",
                    "storage_key",
                )
            ],
        ),
        CheckConstraint(
            "generation > 0 AND input_sha256 ~ '^[a-f0-9]{64}$' AND intent_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )
