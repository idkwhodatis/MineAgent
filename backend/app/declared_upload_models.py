"""030 append-only declared-upload composition; metadata, not source verification."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class DeclaredUploadBasket(Base):
    __tablename__ = "declared_upload_baskets"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key"),
        UniqueConstraint("dataset_id"),
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(
            ["owner_id", "dataset_id", "manifest_sha256"],
            [
                "dataset_versions.owner_id",
                "dataset_versions.id",
                "dataset_versions.content_sha256",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("idempotency_key ~ '^[A-Za-z0-9_.:-]{1,128}$'"),
        CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND manifest_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint("profile='mineagent.declared-upload-basket.v1'"),
    )


class DeclaredUploadBasketMember(Base):
    __tablename__ = "declared_upload_basket_members"
    basket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_manifest_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("basket_id", "source_dataset_id"),
        ForeignKeyConstraint(
            ["owner_id", "basket_id"],
            ["declared_upload_baskets.owner_id", "declared_upload_baskets.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_dataset_id", "source_manifest_sha256"],
            [
                "dataset_versions.owner_id",
                "dataset_versions.id",
                "dataset_versions.content_sha256",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal IN (0,1)"),
        CheckConstraint("source_manifest_sha256 ~ '^[a-f0-9]{64}$'"),
    )
