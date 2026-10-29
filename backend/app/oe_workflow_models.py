"""031 workflow-origin OE relation; declaration alone grants no admission.

Only the additive migration may activate this table. Its deferred graph checks
must bind the successful RD checkpoint, explicit release and frozen next-stage
selection; an automatic child is never an authenticated-user submission.
"""

from datetime import datetime
from uuid import UUID

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


class OEWorkflowSubmission(Base):
    __tablename__ = "oe_workflow_submissions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    submission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rd_stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rd_checkpoint_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_generation: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    release_exposure_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_sha256: Mapped[str] = mapped_column(String(64))
    definition_sha256: Mapped[str] = mapped_column(String(64))
    selection_sha256: Mapped[str] = mapped_column(String(64))
    resolved_inputs_sha256: Mapped[str] = mapped_column(String(64))
    rd_checkpoint_sha256: Mapped[str] = mapped_column(String(64))
    fixed_inputs_sha256: Mapped[str] = mapped_column(String(64))
    submission_origin: Mapped[str] = mapped_column(String(32), default="workflow_stage")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("stage_id"),
        UniqueConstraint("run_id"),
        UniqueConstraint("submission_id"),
        UniqueConstraint("owner_id", "run_id", "id"),
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "rd_stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "input_revision_id"],
            [
                "research_workflow_revisions.owner_id",
                "research_workflow_revisions.workflow_id",
                "research_workflow_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "rd_checkpoint_id"],
            [
                "research_workflow_checkpoints.owner_id",
                "research_workflow_checkpoints.workflow_id",
                "research_workflow_checkpoints.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "submission_id"],
            [
                "oe_structured_submissions.owner_id",
                "oe_structured_submissions.run_id",
                "oe_structured_submissions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["oe_structured_specs.owner_id", "oe_structured_specs.run_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "snapshot_id",
            ],
            [
                "factor_execution_snapshot_publications.owner_id",
                "factor_execution_snapshot_publications.run_id",
                "factor_execution_snapshot_publications.attempt_id",
                "factor_execution_snapshot_publications.generation",
                "factor_execution_snapshot_publications.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "snapshot_id"],
            [
                "factor_execution_snapshot_outcomes.owner_id",
                "factor_execution_snapshot_outcomes.publication_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id"],
            ["factor_versions.owner_id", "factor_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "analysis_id",
            ],
            [
                "factor_direct_analysis_intents.owner_id",
                "factor_direct_analysis_intents.run_id",
                "factor_direct_analysis_intents.attempt_id",
                "factor_direct_analysis_intents.generation",
                "factor_direct_analysis_intents.id",
            ],
            ondelete="RESTRICT",
        ),
        # Existing exposure table has no (owner_id,id) unique. The031 graph
        # predicate must additionally verify its owner and exact release bytes.
        ForeignKeyConstraint(
            ["release_exposure_id"], ["data_usage_exposures.id"], ondelete="RESTRICT"
        ),
        CheckConstraint("submission_origin='workflow_stage'"),
        CheckConstraint(
            "source_generation > 0 AND stage_id <> rd_stage_id AND run_id <> source_run_id"
        ),
        CheckConstraint(
            " AND ".join(
                f"{name} ~ '^[a-f0-9]{{64}}$'"
                for name in (
                    "request_sha256",
                    "config_sha256",
                    "preflight_sha256",
                    "runtime_profile_sha256",
                    "definition_sha256",
                    "selection_sha256",
                    "resolved_inputs_sha256",
                    "rd_checkpoint_sha256",
                    "fixed_inputs_sha256",
                )
            )
        ),
    )
