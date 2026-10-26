"""Unregistered append-only candidate input witnesses; no admission capability."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
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


class FactorCandidateInputInventory(AttemptEvidence, Base):
    __tablename__ = "factor_candidate_input_inventories"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_sha256: Mapped[str] = mapped_column(String(64))
    projection_sha256: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("execution_id", "factor_cutoff_executions"),
        exact_attempt_fk("admission_id", "research_process_admissions"),
        UniqueConstraint("execution_id"),
        UniqueConstraint("admission_id"),
        CheckConstraint("generation > 0 AND file_count = 4"),
        CheckConstraint(
            "input_sha256 ~ '^[a-f0-9]{64}$' AND projection_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorCandidateInputFile(AttemptEvidence, Base):
    __tablename__ = "factor_candidate_input_files"
    inventory_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    destination: Mapped[str] = mapped_column(String(80))
    ordinal: Mapped[int] = mapped_column(Integer)
    object_kind: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    factor_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    research_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    publication_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("inventory_id", "factor_candidate_input_inventories"),
        exact_attempt_fk("publication_id", "research_object_publications"),
        ForeignKeyConstraint(
            ["owner_id", "publication_id", "object_kind", "sha256"],
            [
                "research_object_publications.owner_id",
                "research_object_publications.id",
                "research_object_publications.object_kind",
                "research_object_publications.sha256",
            ],
        ),
        UniqueConstraint("inventory_id", "destination"),
        UniqueConstraint("inventory_id", "ordinal"),
        ForeignKeyConstraint(
            ["owner_id", "factor_object_id", "object_kind", "sha256"],
            [
                "factor_objects.owner_id",
                "factor_objects.id",
                "factor_objects.object_kind",
                "factor_objects.sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "research_object_id", "object_kind", "sha256"],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND ordinal BETWEEN 0 AND 3 AND size_bytes BETWEEN 1 AND 16777216"
        ),
        CheckConstraint("sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint(
            "(ordinal = CASE destination WHEN '/input/listings.json' THEN 0 WHEN '/input/manifest.json' THEN 1 WHEN '/input/observations.json' THEN 2 WHEN '/input/source.py' THEN 3 END) IS TRUE"
        ),
        CheckConstraint(
            "(object_kind = CASE destination WHEN '/input/listings.json' THEN 'factor_prefix_listings' WHEN '/input/manifest.json' THEN 'factor_prefix_manifest' WHEN '/input/observations.json' THEN 'factor_prefix_observations' WHEN '/input/source.py' THEN 'code' END) IS TRUE"
        ),
        CheckConstraint(
            "size_bytes <= CASE destination WHEN '/input/source.py' THEN 131072 WHEN '/input/observations.json' THEN 16777216 ELSE 1048576 END"
        ),
        CheckConstraint(
            "((destination = '/input/source.py' AND object_kind = 'code' AND factor_object_id IS NOT NULL AND research_object_id IS NULL AND publication_id IS NULL) OR (destination IN ('/input/manifest.json','/input/listings.json','/input/observations.json') AND factor_object_id IS NULL AND research_object_id IS NOT NULL AND publication_id IS NOT NULL)) IS TRUE"
        ),
    )
