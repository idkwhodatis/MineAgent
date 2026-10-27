"""Unregistered authenticated observations, not verified execution outcomes."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base
from backend.app.rdagent_models import (
    AttemptEvidence,
    attempt_fk,
    exact_attempt_fk,
    run_fk,
    scope_key,
)


class FactorGenerationObservation(AttemptEvidence, Base):
    __tablename__ = "factor_generation_observations"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_step: Mapped[int] = mapped_column(Integer)
    decision: Mapped[dict] = mapped_column(JSONB)
    decision_sha256: Mapped[str] = mapped_column(String(64))
    qualification: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("start_id", "factor_generation_starts"),
        exact_attempt_fk("controller_launch_id", "factor_controller_launches"),
        UniqueConstraint("start_id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "start_id",
            "logical_step",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "logical_step",
            ],
            [
                f"factor_generation_starts.{key}"
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "logical_step",
                )
            ],
        ),
        CheckConstraint("generation > 0 AND logical_step BETWEEN 0 AND 7"),
        CheckConstraint("decision_sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint(
            "qualification = 'authenticated_observation_not_execution_verification'"
        ),
        CheckConstraint(
            "(jsonb_typeof(decision) = 'object' AND decision->>'schema_version' = 'mineagent.rdagent-adoption-observation.v1') IS TRUE"
        ),
    )


class FactorGenerationBoundary(AttemptEvidence, Base):
    __tablename__ = "factor_generation_boundaries"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    observation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_step: Mapped[int] = mapped_column(Integer)
    boundary: Mapped[str] = mapped_column(String(12))
    next_logical_step: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("controller_launch_id", "factor_controller_launches"),
        UniqueConstraint("start_id"),
        UniqueConstraint("observation_id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "controller_launch_id",
            "start_id",
            name="uq_rd_boundary_execution_actor",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "observation_id",
                "start_id",
                "logical_step",
            ],
            [
                f"factor_generation_observations.{key}"
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "start_id",
                    "logical_step",
                )
            ],
        ),
        CheckConstraint("generation > 0 AND logical_step BETWEEN 0 AND 7"),
        CheckConstraint(
            "((boundary = 'advanced' AND next_logical_step = logical_step + 1 AND next_logical_step BETWEEN 1 AND 7) OR (boundary = 'returned' AND next_logical_step IS NULL)) IS TRUE"
        ),
    )
