"""Durable queued import admission and append-only source/local identity mapping."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class OwnedEvidence:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


def owner_key():
    return UniqueConstraint("owner_id", "id")


def owned_fk(column, table):
    return ForeignKeyConstraint(
        ["owner_id", column], [f"{table}.owner_id", f"{table}.id"]
    )


class FactorImportEnvelope(OwnedEvidence, Base):
    __tablename__ = "factor_import_envelopes"
    archive_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    origin_namespace: Mapped[str] = mapped_column(String(180))
    origin_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    origin_payload_sha256: Mapped[str] = mapped_column(String(64))
    local_bindings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    local_bindings_sha256: Mapped[str] = mapped_column(String(64))
    exposure_history: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        owner_key(),
        UniqueConstraint("owner_id", "archive_sha256"),
        UniqueConstraint("owner_id", "id", "archive_sha256"),
        ForeignKeyConstraint(
            ["owner_id", "archive_object_id", "archive_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
    )


class FactorImportAdmission(OwnedEvidence, Base):
    __tablename__ = "factor_import_admissions"
    archive_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    parser_image_id: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_envelope_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        owner_key(),
        UniqueConstraint("owner_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["owner_id", "result_envelope_id", "archive_sha256"],
            [
                "factor_import_envelopes.owner_id",
                "factor_import_envelopes.id",
                "factor_import_envelopes.archive_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "archive_object_id", "archive_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
        CheckConstraint(
            "status IN ('queued','processing','succeeded','failed','blocked')"
        ),
        CheckConstraint("generation >= 0"),
        CheckConstraint("parser_image_id ~ '^sha256:[0-9a-f]{64}$'"),
        CheckConstraint("(status = 'succeeded') = (result_envelope_id IS NOT NULL)"),
        CheckConstraint(
            "status <> 'processing' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
        ),
        CheckConstraint(
            "status NOT IN ('succeeded','failed') OR (lease_token IS NULL AND lease_expires_at IS NULL)"
        ),
    )


FACTOR_IMPORT_TABLES = (FactorImportEnvelope.__table__, FactorImportAdmission.__table__)
