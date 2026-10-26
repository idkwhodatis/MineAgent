"""Unregistered bounded host-read metadata; raw bytes stay in charged objects."""

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


class FactorSourceReadWitness(AttemptEvidence, Base):
    __tablename__ = "factor_source_read_witnesses"
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parser_profile: Mapped[str] = mapped_column(String(60))
    parser_source_sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_size_bytes: Mapped[int] = mapped_column(Integer)
    source_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_object_kind: Mapped[str] = mapped_column(String(40))
    raw_text_sha256: Mapped[str] = mapped_column(String(64))
    raw_text_bytes: Mapped[int] = mapped_column(Integer)
    normalized_json_sha256: Mapped[str] = mapped_column(String(64))
    normalized_json_bytes: Mapped[int] = mapped_column(Integer)
    selected_first_ordinal: Mapped[int] = mapped_column(Integer)
    part_count: Mapped[int] = mapped_column(Integer)
    witness_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("source_id", "factor_generation_sources"),
        exact_attempt_fk("assembly_id", "factor_response_assemblies"),
        exact_attempt_fk("call_id", "factor_upstream_calls"),
        exact_attempt_fk("controller_launch_id", "factor_controller_launches"),
        UniqueConstraint("source_id"),
        UniqueConstraint("assembly_id"),
        ForeignKeyConstraint(
            ["owner_id", "source_object_id", "source_object_kind", "source_sha256"],
            [
                "factor_objects.owner_id",
                "factor_objects.id",
                "factor_objects.object_kind",
                "factor_objects.sha256",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND part_count BETWEEN 1 AND 6 AND selected_first_ordinal BETWEEN 0 AND 59"
        ),
        CheckConstraint(
            "source_size_bytes BETWEEN 1 AND 131072 AND raw_text_bytes BETWEEN 1 AND 786432 AND normalized_json_bytes BETWEEN 1 AND 2359296"
        ),
        CheckConstraint(
            "source_object_kind = 'code' AND parser_profile = 'mineagent.rdagent-source-parser.v1'"
        ),
        CheckConstraint(
            "parser_source_sha256 = 'a5a6a4a397f86b71caac2b1e056d200411c9d6254dff5e81d84aad8c19d1a583'"
        ),
        CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$' AND raw_text_sha256 ~ '^[a-f0-9]{64}$' AND normalized_json_sha256 ~ '^[a-f0-9]{64}$' AND witness_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorSourceReadPart(AttemptEvidence, Base):
    __tablename__ = "factor_source_read_parts"
    witness_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_part_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    member_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_object_kind: Mapped[str] = mapped_column(String(40))
    response_sha256: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    finish_reason: Mapped[str] = mapped_column(String(8))
    content_sha256: Mapped[str] = mapped_column(String(64))
    content_size_bytes: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("witness_id", "factor_source_read_witnesses"),
        exact_attempt_fk("assembly_part_id", "factor_response_assembly_parts"),
        exact_attempt_fk("member_id", "factor_generation_model_operations"),
        exact_attempt_fk("outcome_id", "research_model_outcomes"),
        ForeignKeyConstraint(
            [
                "owner_id",
                "response_object_id",
                "response_object_kind",
                "response_sha256",
            ],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        UniqueConstraint("witness_id", "ordinal"),
        UniqueConstraint("witness_id", "member_id"),
        CheckConstraint(
            "generation > 0 AND ordinal BETWEEN 0 AND 5 AND content_size_bytes BETWEEN 1 AND 131072"
        ),
        CheckConstraint(
            "response_object_kind = 'model_response' AND finish_reason IN ('length','stop')"
        ),
        CheckConstraint(
            "response_sha256 ~ '^[a-f0-9]{64}$' AND content_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )
