"""Immutable process admission/receipts and separately mutable state projection."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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
from backend.app.process_admission_ddl_draft import GRAMMAR, HASHES
from backend.app.rdagent_persistence_ddl import CONTROLLER_PROFILE_GATE

PROFILE_GATE = (
    "("
    + CONTROLLER_PROFILE_GATE
    + ") OR (schema_version='mineagent.process-admission.v2' AND role='rd_orchestrator' AND profile='mineagent.rdagent-orchestrator.v2' AND ipc_policy='host_ticket' AND ticket_sha256 IS NOT NULL AND ticket_sha256 ~ '^[a-f0-9]{64}$')"
)


class ResearchProcessAdmission(Base):
    __tablename__ = "research_process_admissions"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(40))
    logical_operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    launch_ordinal: Mapped[int] = mapped_column(Integer)
    profile: Mapped[str] = mapped_column(String(60))
    input_sha256: Mapped[str] = mapped_column(String(64))
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    image_id: Mapped[str] = mapped_column(String(71))
    schema_version: Mapped[str] = mapped_column(String(40))
    ipc_policy: Mapped[str] = mapped_column(String(20))
    ticket_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "role",
            "logical_operation_id",
            "launch_ordinal",
            name="uq_research_process_launch",
        ),
        UniqueConstraint("reservation_id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["research_runs.owner_id", "research_runs.id"]
        ),
        ForeignKeyConstraint(
            ["run_id", "generation", "attempt_id"],
            [
                "execution_attempts.run_id",
                "execution_attempts.generation",
                "execution_attempts.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "reservation_id"],
            [
                "research_budget_reservations.owner_id",
                "research_budget_reservations.run_id",
                "research_budget_reservations.attempt_id",
                "research_budget_reservations.generation",
                "research_budget_reservations.id",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND launch_ordinal > 0", name="ck_research_process_ordinals"
        ),
        CheckConstraint(GRAMMAR, name="ck_process_versioned_grammar"),
        CheckConstraint(HASHES, name="ck_process_versioned_hashes"),
        CheckConstraint(PROFILE_GATE, name="ck_process_v2_unregistered"),
    )


class ResearchProcessReceipt(Base):
    __tablename__ = "research_process_receipts"
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("admission_id", "kind"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "admission_id",
            "id",
            "kind",
            name="uq_process_receipt_scoped_kind",
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "admission_id"],
            [
                "research_process_admissions.owner_id",
                "research_process_admissions.run_id",
                "research_process_admissions.attempt_id",
                "research_process_admissions.generation",
                "research_process_admissions.id",
            ],
        ),
        CheckConstraint(
            "kind IN ('launch_intent','launch_identity','controller_child','stop','extraction')",
            name="ck_research_process_receipt_kind",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 16384 AND payload_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_research_process_receipt_payload",
        ),
    )


class ResearchProcessState(Base):
    __tablename__ = "research_process_states"
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="admitted")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "admission_id"],
            [
                "research_process_admissions.owner_id",
                "research_process_admissions.run_id",
                "research_process_admissions.attempt_id",
                "research_process_admissions.generation",
                "research_process_admissions.id",
            ],
        ),
        CheckConstraint(
            "status IN ('admitted','launch_intended','running','uncertain','stopped')",
            name="ck_research_process_state",
        ),
    )
