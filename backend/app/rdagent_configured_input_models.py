"""022 exact configured-input reread metadata; historical018 list-hash stays intact."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
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


class FactorConfiguredInputWitness(AttemptEvidence, Base):
    __tablename__ = "factor_configured_input_witnesses"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_read_witness_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    dataset_manifest_sha256: Mapped[str] = mapped_column(String(64))
    availability_revision_sha256: Mapped[str] = mapped_column(String(64))
    calendar_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    projection_sha256: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSONB)
    witness_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("execution_id", "factor_cutoff_executions"),
        exact_attempt_fk("admission_id", "research_process_admissions"),
        exact_attempt_fk("source_read_witness_id", "factor_source_read_witnesses"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "plan_id"],
            [
                "factor_analysis_plans.owner_id",
                "factor_analysis_plans.run_id",
                "factor_analysis_plans.id",
            ],
        ),
        UniqueConstraint("execution_id"),
        UniqueConstraint("admission_id"),
        CheckConstraint("generation > 0"),
        CheckConstraint(
            "dataset_manifest_sha256 ~ '^[a-f0-9]{64}$' AND availability_revision_sha256 ~ '^[a-f0-9]{64}$' AND calendar_sha256 ~ '^[a-f0-9]{64}$' AND input_sha256 ~ '^[a-f0-9]{64}$' AND projection_sha256 ~ '^[a-f0-9]{64}$' AND witness_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(document)='object' AND octet_length(document::text)<=4096"
        ),
    )
