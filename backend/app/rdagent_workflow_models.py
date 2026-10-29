"""Distinct immutable workflow-child receipt; never a human submission receipt.

Registration and exact graph triggers belong to028. Importing this declaration
neither activates a profile nor permits attaching an existing research run.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class FactorWorkflowSubmission(Base):
    __tablename__ = "factor_workflow_submissions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_canonical: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    selection_sha256: Mapped[str] = mapped_column(String(64))
    resolved_inputs_sha256: Mapped[str] = mapped_column(String(64))
    definition_sha256: Mapped[str] = mapped_column(String(64))
    submission_origin: Mapped[str] = mapped_column(String(32), default="workflow_stage")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "stage_id"),
        UniqueConstraint("owner_id", "run_id"),
        UniqueConstraint("owner_id", "run_id", "id"),
        ForeignKeyConstraint(["owner_id"], ["users.id"]),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "input_revision_id"],
            [
                "research_workflow_revisions.owner_id",
                "research_workflow_revisions.workflow_id",
                "research_workflow_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "spec_id"],
            [
                "factor_research_specs.owner_id",
                "factor_research_specs.run_id",
                "factor_research_specs.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "plan_id"],
            [
                "factor_analysis_plans.owner_id",
                "factor_analysis_plans.run_id",
                "factor_analysis_plans.id",
            ],
        ),
        CheckConstraint("submission_origin='workflow_stage'"),
        CheckConstraint("octet_length(request_canonical) BETWEEN 1 AND 131072"),
        CheckConstraint(
            " AND ".join(
                f"{name} ~ '^[a-f0-9]{{64}}$'"
                for name in (
                    "request_sha256",
                    "preflight_sha256",
                    "runtime_profile_sha256",
                    "config_sha256",
                    "selection_sha256",
                    "resolved_inputs_sha256",
                    "definition_sha256",
                )
            )
        ),
    )
