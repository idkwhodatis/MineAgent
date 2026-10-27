"""026 SDK physical evidence membership; payload retained in charged objects.

No registration/authority here. Migration must add append-only and exact scoped
publication/transport/deadline predicates before any production insertion.
"""

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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


# Keep the declarations independent of broker module initialization. Direct
# broker import may itself be loading models.py and this registry entry.
class BoundRecord:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)


def bound_constraints():
    return (
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
        UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id"),
        CheckConstraint("generation > 0"),
    )


def bound_fk(column, table):
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    return ForeignKeyConstraint(
        [*scope, column], [*(f"{table}.{field}" for field in scope), f"{table}.id"]
    )


def object_fk(prefix):
    return ForeignKeyConstraint(
        [
            "owner_id",
            f"{prefix}_object_id",
            f"{prefix}_object_kind",
            f"{prefix}_sha256",
        ],
        [
            "research_objects.owner_id",
            "research_objects.id",
            "research_objects.object_kind",
            "research_objects.sha256",
        ],
    )


class RDProviderTransportReceipt(BoundRecord, Base):
    __tablename__ = "rd_provider_transport_receipts"
    transport_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    receipt_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    receipt_object_kind: Mapped[str] = mapped_column(String(40))
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        *bound_constraints(),
        bound_fk("transport_id", "research_model_transports"),
        object_fk("receipt"),
        UniqueConstraint("transport_id", "kind"),
        CheckConstraint("kind IN ('intent','started','stopped')"),
        CheckConstraint("receipt_object_kind='rd_provider_process_receipt'"),
        CheckConstraint("receipt_sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint("size_bytes BETWEEN 1 AND 16384"),
    )
