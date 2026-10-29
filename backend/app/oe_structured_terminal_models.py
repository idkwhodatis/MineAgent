"""027 exact native worker/application membership; no installation or authority."""

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


class OEStructuredWorkerOutcome(ScopedEvidence, Base):
    __tablename__ = "oe_structured_worker_outcomes"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    iteration_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    execution_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    engine_outcome_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    evidence_canonical: Mapped[str] = mapped_column(Text)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    event_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        fk((*SCOPE, "epoch_id"), "oe_structured_epochs", (*SCOPE, "id")),
        fk(
            (*SCOPE, "iteration_intent_id"),
            "oe_structured_iteration_intents",
            (*SCOPE, "id"),
        ),
        fk(
            (*SCOPE, "execution_id", "candidate_id", "engine_outcome_id"),
            "oe_structured_engine_outcomes",
            (*SCOPE, "execution_id", "candidate_id", "id"),
        ),
        CheckConstraint(
            "(status='evaluated' AND candidate_id IS NOT NULL AND execution_id IS NOT NULL AND engine_outcome_id IS NOT NULL) OR (status='worker_error' AND candidate_id IS NULL AND execution_id IS NULL AND engine_outcome_id IS NULL)"
        ),
        CheckConstraint(
            "octet_length(evidence_canonical) BETWEEN 1 AND 131072 AND evidence_sha256 ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredTerminalApplication(ScopedEvidence, Base):
    __tablename__ = "oe_structured_terminal_applications"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    control_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    worker_outcome_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), unique=True
    )
    outcome: Mapped[str] = mapped_column(String(16))
    event_canonical: Mapped[str] = mapped_column(Text)
    event_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        UniqueConstraint("epoch_id", "ordinal"),
        fk((*SCOPE, "epoch_id"), "oe_structured_epochs", (*SCOPE, "id")),
        fk(
            (*SCOPE, "control_state_id"), "oe_structured_control_states", (*SCOPE, "id")
        ),
        fk(
            (*SCOPE, "worker_outcome_id"),
            "oe_structured_worker_outcomes",
            (*SCOPE, "id"),
        ),
        CheckConstraint(
            "(ordinal=0 AND outcome='seed' AND worker_outcome_id IS NULL) OR (ordinal BETWEEN 1 AND 2 AND outcome IN ('accepted','rejected','error') AND worker_outcome_id IS NOT NULL)"
        ),
        CheckConstraint(
            "octet_length(event_canonical) BETWEEN 1 AND 1048576 AND event_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )
