"""027 normal controller and SDK membership, distinct from Python024/RD026.

Only immutable membership is declared here. Installation must add the027
cross-row semantics; no model/table import creates a launch capability.
"""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.oe_structured_models import (
    SCOPE,
    ScopedEvidence,
    fk,
    scope_constraints,
    scoped_fk,
)


class OEStructuredEpoch(ScopedEvidence, Base):
    __tablename__ = "oe_structured_epochs"
    controller_admission_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True
    )
    bootstrap_canonical: Mapped[str] = mapped_column(Text)
    bootstrap_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        UniqueConstraint("run_id"),
        ForeignKeyConstraint(
            [*SCOPE, "controller_admission_id"],
            [f"research_process_admissions.{key}" for key in (*SCOPE, "id")],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "bootstrap_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(bootstrap_canonical) BETWEEN 1 AND 262144"
        ),
    )


class OEStructuredControlState(ScopedEvidence, Base):
    """Actual upstream population cursor, distinct from its applied frontier."""

    __tablename__ = "oe_structured_control_states"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    previous_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    frontier: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(16))
    state_canonical: Mapped[str] = mapped_column(Text)
    state_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        scoped_fk("epoch_id", "oe_structured_epochs"),
        scoped_fk("previous_id", "oe_structured_control_states"),
        UniqueConstraint("epoch_id", "sequence"),
        CheckConstraint(
            "sequence BETWEEN 1 AND 5 AND frontier BETWEEN 0 AND 2 AND phase IN ('seed','sampling','applied')"
        ),
        CheckConstraint(
            "((sequence=1 AND phase='seed' AND frontier=0 AND previous_id IS NULL) OR (sequence>1 AND phase<>'seed' AND previous_id IS NOT NULL))"
        ),
        CheckConstraint(
            "state_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(state_canonical) BETWEEN 1 AND 786432"
        ),
    )


class OEStructuredIterationIntent(ScopedEvidence, Base):
    """Host-retained actual sampling/rendered operation, not caller authority."""

    __tablename__ = "oe_structured_iteration_intents"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sampling_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    selection_canonical: Mapped[str] = mapped_column(Text)
    selection_sha256: Mapped[str] = mapped_column(String(64))
    context_canonical: Mapped[str] = mapped_column(Text)
    context_sha256: Mapped[str] = mapped_column(String(64))
    rendered_canonical: Mapped[str] = mapped_column(Text)
    rendered_sha256: Mapped[str] = mapped_column(String(64))
    worker_ticket_sha256: Mapped[str] = mapped_column(String(64))
    # Actual independently observed worker kernel identity, not wire PID.
    worker_identity_canonical: Mapped[str] = mapped_column(Text)
    worker_identity_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        scoped_fk("id", "oe_structured_sampling_intents"),
        fk(
            (*SCOPE, "id"),
            "oe_structured_worker_placements",
            (*SCOPE, "sampling_intent_id"),
        ),
        scoped_fk("epoch_id", "oe_structured_epochs"),
        scoped_fk("parent_candidate_id", "oe_structured_candidates"),
        scoped_fk("sampling_state_id", "oe_structured_control_states"),
        UniqueConstraint("epoch_id", "ordinal"),
        UniqueConstraint("worker_ticket_sha256"),
        CheckConstraint("ordinal BETWEEN 1 AND 2"),
        CheckConstraint(
            "selection_sha256 ~ '^[a-f0-9]{64}$' AND context_sha256 ~ '^[a-f0-9]{64}$' AND rendered_sha256 ~ '^[a-f0-9]{64}$' AND worker_ticket_sha256 ~ '^[a-f0-9]{64}$' AND worker_identity_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "octet_length(selection_canonical) BETWEEN 1 AND 65536 AND octet_length(context_canonical) BETWEEN 1 AND 65536 AND octet_length(rendered_canonical) BETWEEN 1 AND 262144 AND octet_length(worker_identity_canonical) BETWEEN 1 AND 16384"
        ),
    )


class OEStructuredProviderTransport(ScopedEvidence, Base):
    """Single physical SDK transport bound to one real rendered OE operation."""

    __tablename__ = "oe_structured_provider_transports"
    iteration_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    transport_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    model_binding_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        scoped_fk("iteration_intent_id", "oe_structured_iteration_intents"),
        scoped_fk("transport_id", "research_model_transports"),
        scoped_fk("operation_id", "research_model_operations"),
        CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND model_binding_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredProviderReceipt(ScopedEvidence, Base):
    __tablename__ = "oe_structured_provider_receipts"
    membership_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    receipt_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    receipt_object_kind: Mapped[str] = mapped_column(String(40))
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        *scope_constraints(),
        # The membership's primary id is distinct from shared physical transport.
        scoped_fk("membership_id", "oe_structured_provider_transports"),
        fk(
            ("owner_id", "receipt_object_id", "receipt_object_kind", "receipt_sha256"),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        UniqueConstraint("membership_id", "kind"),
        CheckConstraint(
            "kind IN ('intent','started','stopped') AND receipt_object_kind='oe_structured_provider_receipt'"
        ),
        CheckConstraint(
            "receipt_sha256 ~ '^[a-f0-9]{64}$' AND size_bytes BETWEEN 1 AND 16384"
        ),
    )
