"""Unregistered026 seeded declaration; never reinterpret the023 fresh receipt.

026 must install immutable/pre-generation/owner-lock/deferred exact parent
predicates and cross-family owner-key replay dispatch together. Existing FKs alone
do not prove that the factor/code/ordinal belong to the selected parent version.
The application registry does not import this proposal. Importing it defines
metadata only; it does not migrate a database or grant execution authority.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class FactorSeededSubmission(Base):
    __tablename__ = "factor_seeded_submissions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    analysis_plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_canonical: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_sha256: Mapped[str] = mapped_column(String(64))
    parent_factor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_ordinal: Mapped[int] = mapped_column(Integer)
    parent_code_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_code_sha256: Mapped[str] = mapped_column(String(64))
    parent_code_size_bytes: Mapped[int] = mapped_column(Integer)
    parent_definition_sha256: Mapped[str] = mapped_column(String(64))
    parent_provenance_sha256: Mapped[str] = mapped_column(String(64))
    parent_provenance_canonical: Mapped[str] = mapped_column(Text)
    parent_binding_sha256: Mapped[str] = mapped_column(String(64))
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
            ["owner_id", "run_id", "spec_id"],
            [
                "factor_research_specs.owner_id",
                "factor_research_specs.run_id",
                "factor_research_specs.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "analysis_plan_id"],
            [
                "factor_analysis_plans.owner_id",
                "factor_analysis_plans.run_id",
                "factor_analysis_plans.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "parent_factor_id"], ["factors.owner_id", "factors.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "parent_factor_version_id", "parent_definition_sha256"],
            [
                "factor_versions.owner_id",
                "factor_versions.id",
                "factor_versions.definition_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "parent_code_object_id", "parent_code_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
        CheckConstraint(
            "idempotency_key ~ '^[!-~]{1,128}$' AND octet_length(request_canonical) BETWEEN 1 AND 131072"
        ),
        CheckConstraint(
            "parent_ordinal>0 AND parent_code_size_bytes BETWEEN 1 AND 131072 AND octet_length(parent_provenance_canonical) BETWEEN 2 AND 65536"
        ),
        CheckConstraint(
            " AND ".join(
                f"{field} ~ '^[a-f0-9]{{64}}$'"
                for field in (
                    "request_sha256",
                    "preflight_sha256",
                    "config_sha256",
                    "runtime_profile_sha256",
                    "parent_code_sha256",
                    "parent_definition_sha256",
                    "parent_provenance_sha256",
                    "parent_binding_sha256",
                )
            )
        ),
    )
