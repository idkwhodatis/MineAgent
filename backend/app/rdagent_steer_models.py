"""026-owned steer storage declarations; migration registration is external."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class RDSteer(Base):
    __tablename__ = "rd_steers"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    current_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(["owner_id"], ["users.id"]),
        ForeignKeyConstraint(
            ["owner_id", "id", "current_version_id"],
            [
                "rd_steer_versions.owner_id",
                "rd_steer_versions.steer_id",
                "rd_steer_versions.id",
            ],
            name="fk_rd_steer_head",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("revision BETWEEN 1 AND 1000000"),
    )


class RDSteerVersion(Base):
    __tablename__ = "rd_steer_versions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    steer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    content_canonical: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "steer_id", "id"),
        UniqueConstraint("steer_id", "ordinal"),
        ForeignKeyConstraint(
            ["owner_id", "steer_id"],
            ["rd_steers.owner_id", "rd_steers.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 64 AND kind IN ('direction','hypothesis','constraints') AND length(text) BETWEEN 1 AND 2048 AND octet_length(text)<=8192 AND length(btrim(text))>0"
        ),
        CheckConstraint(
            "text_sha256 ~ '^[a-f0-9]{64}$' AND content_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(content_canonical) BETWEEN 1 AND 32768"
        ),
    )
