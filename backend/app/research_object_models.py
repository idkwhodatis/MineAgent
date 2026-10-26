"""Append-only, kind-qualified neutral objects and their physical reservations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class ResearchObject(Base):
    __tablename__ = "research_objects"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    object_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "id", "object_kind", "sha256"),
        UniqueConstraint("owner_id", "object_kind", "sha256"),
        UniqueConstraint("storage_key"),
        CheckConstraint(
            "object_kind IN ('model_request','model_response','process_receipt','factor_response_assembly')"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("size_bytes BETWEEN 1 AND 1048576"),
        CheckConstraint("media_type = 'application/json'"),
        CheckConstraint(
            "storage_key = 'research-objects/' || owner_id::text || '/by-kind/' || object_kind || '/' || sha256 || '.json'"
        ),
    )


class ResearchObjectPublication(Base):
    __tablename__ = "research_object_publications"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_reservation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    object_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    peak_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id"),
        UniqueConstraint("owner_id", "id", "object_kind", "sha256"),
        UniqueConstraint("budget_reservation_id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "budget_reservation_id"],
            [
                f"research_budget_reservations.{x}"
                for x in ("owner_id", "run_id", "attempt_id", "generation", "id")
            ],
        ),
        CheckConstraint("generation > 0"),
        CheckConstraint(
            "object_kind IN ('model_request','model_response','process_receipt','factor_response_assembly')"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("size_bytes BETWEEN 1 AND 1048576"),
        CheckConstraint("peak_bytes = size_bytes * 2 + 8192"),
        CheckConstraint(
            "storage_key = 'research-objects/' || owner_id::text || '/by-kind/' || object_kind || '/' || sha256 || '.json'"
        ),
    )


class ResearchObjectOutcome(Base):
    __tablename__ = "research_object_outcomes"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    object_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    journal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "publication_id", "object_kind", "sha256"],
            [
                f"research_object_publications.{x}"
                for x in ("owner_id", "id", "object_kind", "sha256")
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "object_id", "object_kind", "sha256"],
            [
                f"research_objects.{x}"
                for x in ("owner_id", "id", "object_kind", "sha256")
            ],
        ),
        CheckConstraint("journal_sha256 ~ '^[0-9a-f]{64}$'"),
    )
