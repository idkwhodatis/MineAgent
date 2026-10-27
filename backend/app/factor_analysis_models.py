"""Revision022 declarations and measured direct analysis, not legacy snapshots.

No database DDL or runtime activation occurs merely by importing these models.
The revision installs immutable/pre-generation and exact result-chain predicates.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class FactorAnalysisPlan(Base):
    __tablename__ = "factor_analysis_plans"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    config_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    objective: Mapped[dict] = mapped_column(JSONB)
    objective_canonical: Mapped[str] = mapped_column(Text)
    objective_sha256: Mapped[str] = mapped_column(String(64))
    source_policy: Mapped[dict] = mapped_column(JSONB)
    source_policy_sha256: Mapped[str] = mapped_column(String(64))
    label_source_dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id")
    )
    label_source_manifest_sha256: Mapped[str] = mapped_column(String(64))
    qlib_image_id: Mapped[str] = mapped_column(String(71))
    qlib_installed_parent_image_id: Mapped[str] = mapped_column(String(71))
    analysis_dependency_lock_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id"),
        CheckConstraint(
            "qlib_image_id ~ '^sha256:[a-f0-9]{64}$' AND qlib_installed_parent_image_id ~ '^sha256:[a-f0-9]{64}$' AND analysis_dependency_lock_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        UniqueConstraint("owner_id", "run_id", "id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "spec_id"],
            [
                "factor_research_specs.owner_id",
                "factor_research_specs.run_id",
                "factor_research_specs.id",
            ],
        ),
        CheckConstraint(
            "config_sha256 ~ '^[a-f0-9]{64}$' AND input_sha256 ~ '^[a-f0-9]{64}$' AND objective_sha256 ~ '^[a-f0-9]{64}$' AND source_policy_sha256 ~ '^[a-f0-9]{64}$' AND label_source_manifest_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(objective)='object' AND octet_length(objective::text)<=65536 AND octet_length(objective_canonical) BETWEEN 1 AND 65536"
        ),
        CheckConstraint(
            "jsonb_typeof(source_policy)='object' AND octet_length(source_policy::text)<=65536"
        ),
    )


class FactorDirectAnalysisIntent(Base):
    __tablename__ = "factor_direct_analysis_intents"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    label_source_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    label_manifest: Mapped[dict] = mapped_column(JSONB)
    label_manifest_sha256: Mapped[str] = mapped_column(String(64))
    label_recipe_sha256: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[dict] = mapped_column(JSONB)
    protocol_sha256: Mapped[str] = mapped_column(String(64))
    analysis_input_sha256: Mapped[str] = mapped_column(String(64))
    analysis_input_size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id"),
        UniqueConstraint("plan_id", "snapshot_id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "plan_id"],
            [
                "factor_analysis_plans.owner_id",
                "factor_analysis_plans.run_id",
                "factor_analysis_plans.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "snapshot_id"],
            [
                "factor_execution_snapshot_outcomes.owner_id",
                "factor_execution_snapshot_outcomes.publication_id",
            ],
        ),
        ForeignKeyConstraint(
            ["run_id", "generation", "attempt_id"],
            [
                "execution_attempts.run_id",
                "execution_attempts.generation",
                "execution_attempts.id",
            ],
        ),
        CheckConstraint(
            "generation>0 AND analysis_input_size_bytes BETWEEN 1 AND 1048576"
        ),
        CheckConstraint(
            "analysis_input_sha256 ~ '^[a-f0-9]{64}$' AND label_manifest_sha256 ~ '^[a-f0-9]{64}$' AND label_recipe_sha256 ~ '^[a-f0-9]{64}$' AND protocol_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(label_manifest)='object' AND octet_length(label_manifest::text)<=65536"
        ),
        CheckConstraint(
            "jsonb_typeof(protocol)='object' AND octet_length(protocol::text)<=1048576"
        ),
    )


class FactorDirectAnalysisOutcome(Base):
    __tablename__ = "factor_direct_analysis_outcomes"
    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    launch_identity_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    launch_identity_receipt_sha256: Mapped[str] = mapped_column(String(64))
    launch_identity_kind: Mapped[str] = mapped_column(String(30))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_receipt_sha256: Mapped[str] = mapped_column(String(64))
    stop_kind: Mapped[str] = mapped_column(String(30))
    extraction_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    extraction_receipt_sha256: Mapped[str] = mapped_column(String(64))
    extraction_kind: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict] = mapped_column(JSONB)
    result_canonical: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str] = mapped_column(String(64))
    result_size_bytes: Mapped[int] = mapped_column(BigInteger)
    measurement: Mapped[dict] = mapped_column(JSONB)
    measurement_canonical: Mapped[str] = mapped_column(Text)
    measurement_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "analysis_id"
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "analysis_id"],
            [
                "factor_direct_analysis_intents." + k
                for k in ("owner_id", "run_id", "attempt_id", "generation", "id")
            ],
        ),
        *tuple(
            ForeignKeyConstraint(
                [
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "admission_id",
                    field,
                    kind,
                ],
                [
                    "research_process_receipts." + k
                    for k in (
                        "owner_id",
                        "run_id",
                        "attempt_id",
                        "generation",
                        "admission_id",
                        "id",
                        "kind",
                    )
                ],
            )
            for field, kind in (
                ("launch_identity_receipt_id", "launch_identity_kind"),
                ("stop_receipt_id", "stop_kind"),
                ("extraction_receipt_id", "extraction_kind"),
            )
        ),
        CheckConstraint(
            "generation>0 AND launch_identity_kind='launch_identity' AND stop_kind='stop' AND extraction_kind='extraction'"
        ),
        CheckConstraint(
            "result_sha256 ~ '^[a-f0-9]{64}$' AND measurement_sha256 ~ '^[a-f0-9]{64}$' AND launch_identity_receipt_sha256 ~ '^[a-f0-9]{64}$' AND stop_receipt_sha256 ~ '^[a-f0-9]{64}$' AND extraction_receipt_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "result_size_bytes BETWEEN 1 AND 1048576 AND jsonb_typeof(result)='object' AND octet_length(result::text)<=1048576 AND octet_length(result_canonical) BETWEEN 1 AND 1048576"
        ),
        CheckConstraint(
            "jsonb_typeof(measurement)='object' AND octet_length(measurement::text)<=65536 AND octet_length(measurement_canonical) BETWEEN 1 AND 65536"
        ),
    )
