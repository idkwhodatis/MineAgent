"""Disabled immutable worker assignment/closure tables, never registered here."""

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.openevolve_models import SCOPE, AttemptEvidence, fk, scope_constraints


class SearchWorkerTicket(AttemptEvidence, Base):
    __tablename__ = "search_worker_tickets"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        UniqueConstraint("ticket_sha256"),
        UniqueConstraint(*SCOPE, "epoch_id", "admission_id", "intent_id", "id"),
        fk(
            [*SCOPE, "epoch_id", "admission_id"],
            "strategy_search_epochs",
            [*SCOPE, "id", "admission_id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            [*SCOPE, "controller_receipt_id"],
            "search_controller_event_receipts",
            [*SCOPE, "id"],
        ),
        CheckConstraint("ticket_sha256 ~ '^[0-9a-f]{64}$'"),
    )


class SearchWorkerAssignment(AttemptEvidence, Base):
    __tablename__ = "search_worker_assignments"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    expected_identity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    host_pid: Mapped[int] = mapped_column(Integer)
    start_ticks: Mapped[int] = mapped_column(BigInteger)
    pid_namespace: Mapped[str] = mapped_column(String(128))
    cgroup: Mapped[str] = mapped_column(String(4096))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        UniqueConstraint("ticket_id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "expected_identity_id"],
            "search_worker_expected_identities",
            [*SCOPE, "epoch_id", "intent_id", "id"],
        ),
        UniqueConstraint(*SCOPE, "epoch_id", "intent_id", "id"),
        fk(
            [*SCOPE, "epoch_id", "admission_id", "intent_id", "ticket_id"],
            "search_worker_tickets",
            [*SCOPE, "epoch_id", "admission_id", "intent_id", "id"],
        ),
        CheckConstraint(
            "host_pid>0 AND start_ticks>0 AND length(pid_namespace)>0 AND length(cgroup)>0"
        ),
    )


class SearchWorkerAssignmentClosure(AttemptEvidence, Base):
    __tablename__ = "search_worker_assignment_closures"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assignment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    native_iteration: Mapped[int] = mapped_column(Integer)
    worker_outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("assignment_id"),
        UniqueConstraint("worker_outcome_id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "assignment_id"],
            "search_worker_assignments",
            [*SCOPE, "epoch_id", "intent_id", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "native_iteration", "worker_outcome_id"],
            "search_worker_outcomes",
            [*SCOPE, "epoch_id", "native_iteration", "id"],
        ),
    )


class SearchWorkerExpectedIdentity(AttemptEvidence, Base):
    """No issuance API yet: trusted pool-placement observation is not implemented."""

    __tablename__ = "search_worker_expected_identities"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    host_pid: Mapped[int] = mapped_column(Integer)
    start_ticks: Mapped[int] = mapped_column(BigInteger)
    pid_namespace: Mapped[str] = mapped_column(String(128))
    cgroup: Mapped[str] = mapped_column(String(4096))
    evidence_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evidence_kind: Mapped[str] = mapped_column(
        String(40), default="search_worker_identity"
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        UniqueConstraint("ticket_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "intent_id", "id"),
        fk(
            [*SCOPE, "epoch_id", "admission_id", "intent_id", "ticket_id"],
            "search_worker_tickets",
            [*SCOPE, "epoch_id", "admission_id", "intent_id", "id"],
        ),
        fk(
            ["owner_id", "evidence_object_id", "evidence_kind", "evidence_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "host_pid>0 AND start_ticks>0 AND length(pid_namespace)>0 AND length(cgroup)>0 AND evidence_kind='search_worker_identity' AND evidence_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )
