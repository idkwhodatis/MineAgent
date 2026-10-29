"""Partial native submission stages exist before a completed rendered intent.

A sampling row cannot authorize an SDK or engine call. Placement records only
an independently observed exact scheduled worker; the final rendered intent
must reference both. No empty/fabricated prompt or kernel identity is stored.
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.oe_structured_models import (
    SCOPE,
    ScopedEvidence,
    scope_constraints,
    scoped_fk,
)


class OEStructuredSamplingIntent(ScopedEvidence, Base):
    __tablename__ = "oe_structured_sampling_intents"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sampling_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    parent_candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    mutation_reservation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    selection_canonical: Mapped[str] = mapped_column(Text)
    selection_sha256: Mapped[str] = mapped_column(String(64))
    event_sha256: Mapped[str] = mapped_column(String(64))
    worker_ticket_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    __table_args__ = (
        *scope_constraints(),
        scoped_fk("epoch_id", "oe_structured_epochs"),
        scoped_fk("sampling_state_id", "oe_structured_control_states"),
        scoped_fk("parent_candidate_id", "oe_structured_candidates"),
        scoped_fk("mutation_reservation_id", "research_budget_reservations"),
        UniqueConstraint("epoch_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 2"),
        CheckConstraint(
            "selection_sha256 ~ '^[a-f0-9]{64}$' AND event_sha256 ~ '^[a-f0-9]{64}$' AND worker_ticket_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint("octet_length(selection_canonical) BETWEEN 1 AND 65536"),
    )


class OEStructuredWorkerPlacement(ScopedEvidence, Base):
    __tablename__ = "oe_structured_worker_placements"
    sampling_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    placement_canonical: Mapped[str] = mapped_column(Text)
    placement_sha256: Mapped[str] = mapped_column(String(64))
    worker_identity_canonical: Mapped[str] = mapped_column(Text)
    worker_identity_sha256: Mapped[str] = mapped_column(String(64))
    controller_identity_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        scoped_fk("sampling_intent_id", "oe_structured_sampling_intents"),
        UniqueConstraint(*SCOPE, "sampling_intent_id"),
        CheckConstraint(
            "placement_sha256 ~ '^[a-f0-9]{64}$' AND worker_identity_sha256 ~ '^[a-f0-9]{64}$' AND controller_identity_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "octet_length(placement_canonical) BETWEEN 1 AND 8192 AND octet_length(worker_identity_canonical) BETWEEN 1 AND 16384"
        ),
    )
