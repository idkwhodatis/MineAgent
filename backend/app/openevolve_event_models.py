"""Additional unregistered controller receipt table; no model registry import."""

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.openevolve_models import SCOPE, AttemptEvidence, fk, scope_constraints


class SearchControllerEventReceipt(AttemptEvidence, Base):
    __tablename__ = "search_controller_event_receipts"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event_kind: Mapped[str] = mapped_column(String(24))
    native_iteration: Mapped[int] = mapped_column(Integer)
    event_sha256: Mapped[str] = mapped_column(String(64))
    event_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event_object_kind: Mapped[str] = mapped_column(
        String(40), default="search_controller_event"
    )
    command_sha256: Mapped[str] = mapped_column(String(64))
    control_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    control_sequence: Mapped[int] = mapped_column(Integer)
    control_frontier: Mapped[int] = mapped_column(Integer)
    control_state_sha256: Mapped[str] = mapped_column(String(64))
    intent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    terminal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("epoch_id", "event_kind", "native_iteration"),
        UniqueConstraint("admission_id", "event_sha256"),
        UniqueConstraint("control_id"),
        fk(
            [*SCOPE, "epoch_id", "admission_id"],
            "strategy_search_epochs",
            [*SCOPE, "id", "admission_id"],
        ),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "control_frontier",
                "control_id",
                "control_state_sha256",
            ],
            "search_control_transitions",
            [*SCOPE, "epoch_id", "frontier", "id", "state_sha256"],
        ),
        fk(
            [*SCOPE, "epoch_id", "control_sequence", "control_id"],
            "search_control_transitions",
            [*SCOPE, "epoch_id", "sequence", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "native_iteration", "terminal_id"],
            "search_terminal_applications",
            [*SCOPE, "epoch_id", "frontier", "id"],
        ),
        fk(
            ["owner_id", "event_object_id", "event_object_kind", "event_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "event_object_kind='search_controller_event' AND event_sha256 ~ '^[0-9a-f]{64}$' AND command_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "control_sequence BETWEEN 1 AND 5 AND control_frontier BETWEEN 0 AND 2 AND control_state_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "(event_kind='iteration_intent' AND native_iteration BETWEEN 1 AND 2 AND intent_id IS NOT NULL AND terminal_id IS NULL) OR (event_kind='terminal_applied' AND native_iteration BETWEEN 0 AND 2 AND intent_id IS NULL AND terminal_id IS NOT NULL AND control_frontier=native_iteration)"
        ),
    )
