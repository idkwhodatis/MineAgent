"""Revision023 immutable submission receipt, separate from the run config digest.

This model alone neither registers a migration nor enables public submission.
The run keeps its historical request_sha256=config.sha256 invariant. The receipt
retains the exact user declaration including objective and source policy instead.
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


class FactorResearchSubmission(Base):
    __tablename__ = "factor_research_submissions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_canonical: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_sha256: Mapped[str] = mapped_column(String(64))
    catalog_revision: Mapped[str] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key"),
        UniqueConstraint("owner_id", "run_id"),
        UniqueConstraint("owner_id", "run_id", "id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["research_runs.owner_id", "research_runs.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "plan_id"],
            [
                "factor_analysis_plans.owner_id",
                "factor_analysis_plans.run_id",
                "factor_analysis_plans.id",
            ],
        ),
        CheckConstraint("idempotency_key ~ '^[!-~]{1,128}$'"),
        CheckConstraint("octet_length(request_canonical) BETWEEN 1 AND 131072"),
        CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND preflight_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$' AND runtime_profile_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint("catalog_revision='configured_rd_single_output_session_v1'"),
    )
