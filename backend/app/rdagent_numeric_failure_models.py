"""027 exact failed-evaluation link. No registration or migration activation here."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint
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

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


class FactorNumericFailureFeedback(AttemptEvidence, Base):
    __tablename__ = "factor_numeric_failure_feedbacks"
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evaluation_outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    cutoff_execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    final_decision_call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rejection_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rejection_kind: Mapped[str] = mapped_column(String(30), default="rejection")
    rejection_sha256: Mapped[str] = mapped_column(String(64))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_kind: Mapped[str] = mapped_column(String(30), default="stop")
    stop_sha256: Mapped[str] = mapped_column(String(64))
    feedback_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    feedback_object_kind: Mapped[str] = mapped_column(
        String(40), default="factor_generation_feedback"
    )
    feedback_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("evaluation_id"),
        UniqueConstraint("evaluation_outcome_id"),
        UniqueConstraint("cutoff_execution_id"),
        exact_attempt_fk("evaluation_id", "factor_evaluation_starts"),
        exact_attempt_fk("evaluation_outcome_id", "factor_evaluation_outcomes"),
        exact_attempt_fk("cutoff_execution_id", "factor_cutoff_executions"),
        exact_attempt_fk("candidate_admission_id", "research_process_admissions"),
        exact_attempt_fk("controller_launch_id", "factor_controller_launches"),
        exact_attempt_fk("final_decision_call_id", "factor_upstream_calls"),
        *(
            ForeignKeyConstraint(
                [
                    *SCOPE,
                    "candidate_admission_id",
                    prefix + "_receipt_id",
                    prefix + "_kind",
                ],
                [
                    "research_process_receipts." + field
                    for field in (*SCOPE, "admission_id", "id", "kind")
                ],
            )
            for prefix in ("rejection", "stop")
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "feedback_object_id",
                "feedback_object_kind",
                "feedback_sha256",
            ],
            [
                "research_objects." + field
                for field in ("owner_id", "id", "object_kind", "sha256")
            ],
        ),
        CheckConstraint(
            "generation>0 AND rejection_kind='rejection' AND stop_kind='stop'"
        ),
        CheckConstraint(
            "rejection_sha256 ~ '^[a-f0-9]{64}$' AND stop_sha256 ~ '^[a-f0-9]{64}$' AND feedback_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint("feedback_object_kind='factor_generation_feedback'"),
    )
