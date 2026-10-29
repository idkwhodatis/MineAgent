"""Unregistered source-side input storage models; assembler owns activation.

No legacy FactorObject alias and no large raw BYTEA outside physical accounting.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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


class ResearchInputObject(Base):
    __tablename__ = "research_input_objects"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "id", "sha256"),
        UniqueConstraint("owner_id", "storage_key"),
        ForeignKeyConstraint(["owner_id"], ["users.id"]),
        CheckConstraint(
            "kind IN ('raw_external','normalized_external','normalized_feedback')"
        ),
        CheckConstraint("sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint(
            "(kind='raw_external' AND size_bytes BETWEEN 0 AND 65536) OR (kind IN ('normalized_external','normalized_feedback') AND size_bytes BETWEEN 1 AND 32768)"
        ),
        CheckConstraint(
            "storage_key = 'factors/' || owner_id::text || '/input-objects/' || sha256 || CASE WHEN kind='raw_external' THEN '.raw' WHEN kind='normalized_feedback' THEN '.feedback.json' ELSE '.json' END"
        ),
    )


class ResearchInputDerivation(Base):
    __tablename__ = "research_input_derivations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    operation: Mapped[str] = mapped_column(String(40))
    source_canonical: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    mapping_canonical: Mapped[str | None] = mapped_column(Text)
    mapping_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    raw_object_sha256: Mapped[str | None] = mapped_column(String(64))
    source_workflow_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_stage_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_child_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "workflow_id", "stage_id", "id"),
        UniqueConstraint("stage_id"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "raw_object_id", "raw_object_sha256"],
            [
                "research_input_objects.owner_id",
                "research_input_objects.id",
                "research_input_objects.sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_workflow_id", "source_stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_child_run_id"],
            ["research_runs.owner_id", "research_runs.id"],
        ),
        CheckConstraint(
            "operation IN ('external_normalization','experiment_feedback_projection')"
        ),
        CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(source_canonical) BETWEEN 1 AND 8192"
        ),
        CheckConstraint(
            "(operation='external_normalization' AND raw_object_id IS NOT NULL AND raw_object_sha256 IS NOT NULL AND mapping_canonical IS NOT NULL AND mapping_sha256 IS NOT NULL AND source_workflow_id IS NULL AND source_stage_id IS NULL AND source_child_run_id IS NULL) OR (operation='experiment_feedback_projection' AND raw_object_id IS NULL AND raw_object_sha256 IS NULL AND mapping_canonical IS NULL AND mapping_sha256 IS NULL AND source_workflow_id IS NOT NULL AND source_stage_id IS NOT NULL AND source_child_run_id IS NOT NULL)"
        ),
        CheckConstraint(
            "mapping_canonical IS NULL OR (octet_length(mapping_canonical) BETWEEN 1 AND 4096 AND mapping_sha256 ~ '^[a-f0-9]{64}$')"
        ),
    )


class ResearchInputRecord(Base):
    __tablename__ = "research_input_records"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(String(40))
    current_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(["owner_id"], ["users.id"]),
        ForeignKeyConstraint(
            ["owner_id", "id", "current_version_id"],
            [
                "research_input_versions.owner_id",
                "research_input_versions.record_id",
                "research_input_versions.id",
            ],
            use_alter=True,
            name="fk_research_input_current_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "origin IN ('normalized_external_proposal','experiment_feedback_proposal')"
        ),
        CheckConstraint("revision BETWEEN 1 AND 1000000"),
    )


class ResearchInputVersion(Base):
    __tablename__ = "research_input_versions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    record_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    derivation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    item_ordinal: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    content_canonical: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "record_id", "id"),
        UniqueConstraint("record_id", "ordinal"),
        UniqueConstraint("derivation_id", "item_ordinal"),
        ForeignKeyConstraint(
            ["owner_id", "record_id"],
            ["research_input_records.owner_id", "research_input_records.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "derivation_id"],
            ["research_input_derivations.owner_id", "research_input_derivations.id"],
        ),
        ForeignKeyConstraint(
            ["owner_id", "object_id", "content_sha256"],
            [
                "research_input_objects.owner_id",
                "research_input_objects.id",
                "research_input_objects.sha256",
            ],
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 64 AND item_ordinal BETWEEN 0 AND 31"),
        CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(content_canonical) BETWEEN 1 AND 32768"
        ),
    )


class ResearchInputDerivationEvent(Base):
    __tablename__ = "research_input_derivation_events"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    derivation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(80))
    outputs_canonical: Mapped[str] = mapped_column(Text)
    outputs_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("derivation_id", "ordinal"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id", "derivation_id"],
            [
                "research_input_derivations.owner_id",
                "research_input_derivations.workflow_id",
                "research_input_derivations.stage_id",
                "research_input_derivations.id",
            ],
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 3"),
        CheckConstraint(
            "state IN ('prepared','started','succeeded','rejected','failed','cancelled','interrupted')"
        ),
        CheckConstraint(
            "(state IN ('prepared','started','succeeded') AND error_code IS NULL) OR (state IN ('rejected','failed','cancelled','interrupted') AND error_code IS NOT NULL AND error_code ~ '^[a-z][a-z0-9_]{0,79}$')"
        ),
        CheckConstraint(
            "outputs_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(outputs_canonical) BETWEEN 2 AND 16384"
        ),
    )
