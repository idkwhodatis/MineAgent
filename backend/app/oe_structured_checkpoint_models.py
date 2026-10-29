"""Normal027 actual checkpoint transfer membership, without shared installation.

Native checkpoint bytes remain immutable research objects. These rows bind the
original held inventory, ordered chunks and exact control cursor, not a fabricated
serialization or a new process/restart capability.
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.oe_structured_models import (
    SCOPE,
    ScopedEvidence,
    fk,
    scope_constraints,
)


def _scoped(field, table):
    return fk((*SCOPE, field), table, (*SCOPE, "id"))


def _object(id_field, kind_field, sha_field):
    return fk(
        ("owner_id", id_field, kind_field, sha_field),
        "research_objects",
        ("owner_id", "id", "object_kind", "sha256"),
    )


class OEStructuredCheckpointIntent(ScopedEvidence, Base):
    __tablename__ = "oe_structured_checkpoint_intents"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    control_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    save_ordinal: Mapped[int] = mapped_column(Integer)
    frontier: Mapped[int] = mapped_column(Integer)
    state_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        _scoped("epoch_id", "oe_structured_epochs"),
        _scoped("control_state_id", "oe_structured_control_states"),
        UniqueConstraint("epoch_id", "save_ordinal"),
        CheckConstraint(
            "save_ordinal BETWEEN 1 AND 4 AND frontier BETWEEN 0 AND 2 AND state_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredCheckpointUpload(ScopedEvidence, Base):
    __tablename__ = "oe_structured_checkpoint_uploads"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    inventory_canonical: Mapped[str] = mapped_column(Text)
    inventory_sha256: Mapped[str] = mapped_column(String(64))
    total_bytes: Mapped[int] = mapped_column(Integer)
    file_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        *scope_constraints(),
        _scoped("epoch_id", "oe_structured_epochs"),
        _scoped("intent_id", "oe_structured_checkpoint_intents"),
        _scoped("reservation_id", "research_budget_reservations"),
        CheckConstraint(
            "total_bytes BETWEEN 4 AND 8388608 AND file_count BETWEEN 4 AND 11 AND chunk_count BETWEEN 4 AND 88 AND chunk_count >= file_count"
        ),
        CheckConstraint(
            "octet_length(inventory_canonical) BETWEEN 1 AND 8192 AND inventory_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredCheckpointChunk(ScopedEvidence, Base):
    __tablename__ = "oe_structured_checkpoint_chunks"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upload_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(64))
    offset_bytes: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_sha256: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(
        String(40), default="oe_structured_checkpoint_chunk"
    )
    object_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        _scoped("epoch_id", "oe_structured_epochs"),
        _scoped("upload_id", "oe_structured_checkpoint_uploads"),
        _object("object_id", "object_kind", "object_sha256"),
        UniqueConstraint("upload_id", "sequence"),
        UniqueConstraint("upload_id", "file_name", "offset_bytes"),
        CheckConstraint(
            "sequence BETWEEN 0 AND 87 AND offset_bytes IN (0,65536,131072,196608,262144,327680,393216,458752) AND size_bytes BETWEEN 1 AND 65536"
        ),
        CheckConstraint(
            "file_name IN ('metadata.json','best_program.json','best_program_info.json') OR file_name ~ '^programs/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}[.]json$'"
        ),
        CheckConstraint(
            "object_kind='oe_structured_checkpoint_chunk' AND chunk_sha256 ~ '^[a-f0-9]{64}$' AND object_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredCheckpointCompletion(ScopedEvidence, Base):
    __tablename__ = "oe_structured_checkpoint_completions"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upload_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    inventory_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inventory_object_kind: Mapped[str] = mapped_column(
        String(40), default="oe_structured_checkpoint_inventory"
    )
    inventory_object_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        _scoped("epoch_id", "oe_structured_epochs"),
        _scoped("upload_id", "oe_structured_checkpoint_uploads"),
        _object(
            "inventory_object_id", "inventory_object_kind", "inventory_object_sha256"
        ),
        CheckConstraint(
            "inventory_object_kind='oe_structured_checkpoint_inventory' AND inventory_object_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredCheckpointPublication(ScopedEvidence, Base):
    __tablename__ = "oe_structured_checkpoint_publications"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    completion_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    control_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    state_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        _scoped("epoch_id", "oe_structured_epochs"),
        _scoped("intent_id", "oe_structured_checkpoint_intents"),
        _scoped("completion_id", "oe_structured_checkpoint_completions"),
        _scoped("control_state_id", "oe_structured_control_states"),
        CheckConstraint("state_sha256 ~ '^[a-f0-9]{64}$'"),
    )
