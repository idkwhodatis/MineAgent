"""032 exact retained normal OE note attachment; not release creation authority."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base


class NormalOENotePin(Base):
    __tablename__ = "research_note_normal_oe_pins"

    pin_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_note_evidence_pins.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    engine_outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_exposure_id: Mapped[UUID] = mapped_column(
        ForeignKey("data_usage_exposures.id", ondelete="RESTRICT")
    )
    market_exposure_id: Mapped[UUID] = mapped_column(
        ForeignKey("data_usage_exposures.id", ondelete="RESTRICT")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "execution_id"],
            [
                f"oe_structured_executions.{key}"
                for key in ("owner_id", "run_id", "attempt_id", "generation", "id")
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "engine_outcome_id"],
            [
                f"oe_structured_engine_outcomes.{key}"
                for key in ("owner_id", "run_id", "attempt_id", "generation", "id")
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("generation=1"),
        CheckConstraint("factor_exposure_id<>market_exposure_id"),
    )
