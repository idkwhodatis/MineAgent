"""025 immutable structured strategy and execution graph, not legacy StrategyVersion."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
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

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


class StructuredStrategyVersion(Base):
    __tablename__ = "structured_strategy_versions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    program: Mapped[dict] = mapped_column(JSONB)
    program_canonical: Mapped[str] = mapped_column(Text)
    program_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(["owner_id"], ["users.id"]),
        ForeignKeyConstraint(
            ["owner_id", "parent_version_id"],
            [
                "structured_strategy_versions.owner_id",
                "structured_strategy_versions.id",
            ],
        ),
        CheckConstraint(
            "program_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(program_canonical) BETWEEN 1 AND 65536 AND jsonb_typeof(program)='object'"
        ),
    )


class StructuredStrategySpec(Base):
    __tablename__ = "structured_strategy_specs"
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    structured_strategy_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    config: Mapped[dict] = mapped_column(JSONB)
    config_canonical: Mapped[str] = mapped_column(Text)
    config_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["research_runs.owner_id", "research_runs.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "structured_strategy_version_id"],
            [
                "structured_strategy_versions.owner_id",
                "structured_strategy_versions.id",
            ],
        ),
        CheckConstraint(
            "config_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(config_canonical) BETWEEN 1 AND 1048576 AND jsonb_typeof(config)='object'"
        ),
    )


class StructuredExecution(Base):
    __tablename__ = "factor_strategy_executions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    structured_strategy_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    strategy_input: Mapped[dict] = mapped_column(JSONB)
    strategy_input_canonical: Mapped[str] = mapped_column(Text)
    strategy_input_sha256: Mapped[str] = mapped_column(String(64))
    strategy_input_size_bytes: Mapped[int] = mapped_column(BigInteger)
    market_sha256: Mapped[str] = mapped_column(String(64))
    market_size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(*SCOPE, "id"),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("run_id", "ordinal"),
        UniqueConstraint("run_id", "snapshot_id"),
        ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["structured_strategy_specs.owner_id", "structured_strategy_specs.run_id"],
        ),
        ForeignKeyConstraint(
            ["run_id", "generation", "attempt_id"],
            [
                "execution_attempts.run_id",
                "execution_attempts.generation",
                "execution_attempts.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "structured_strategy_version_id"],
            [
                "structured_strategy_versions.owner_id",
                "structured_strategy_versions.id",
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
            ["owner_id", "factor_version_id"],
            ["factor_versions.owner_id", "factor_versions.id"],
        ),
        CheckConstraint(
            "generation=1 AND ordinal BETWEEN 0 AND 1 AND strategy_input_sha256 ~ '^[a-f0-9]{64}$' AND market_sha256 ~ '^[a-f0-9]{64}$' AND strategy_input_size_bytes BETWEEN 1 AND 1048576 AND octet_length(strategy_input_canonical)=strategy_input_size_bytes AND jsonb_typeof(strategy_input)='object' AND market_size_bytes BETWEEN 1 AND 33554432"
        ),
    )


class StructuredExecutionOutcome(Base):
    __tablename__ = "factor_strategy_execution_outcomes"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    launch_identity_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    launch_identity_kind: Mapped[str] = mapped_column(
        String(30), default="launch_identity"
    )
    launch_identity_sha256: Mapped[str] = mapped_column(String(64))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_kind: Mapped[str] = mapped_column(String(30), default="stop")
    stop_sha256: Mapped[str] = mapped_column(String(64))
    extraction_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    extraction_kind: Mapped[str] = mapped_column(String(30), default="extraction")
    extraction_sha256: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    result_canonical: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str] = mapped_column(String(64))
    result_size_bytes: Mapped[int] = mapped_column(BigInteger)
    decision_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(*SCOPE, "execution_id"),
        ForeignKeyConstraint(
            [*SCOPE, "execution_id"],
            ["factor_strategy_executions." + x for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id"],
            ["research_process_admissions." + x for x in (*SCOPE, "id")],
        ),
        ForeignKeyConstraint(
            [
                *SCOPE,
                "admission_id",
                "launch_identity_receipt_id",
                "launch_identity_kind",
            ],
            [
                "research_process_receipts." + x
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        CheckConstraint("launch_identity_kind='launch_identity'"),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id", "stop_receipt_id", "stop_kind"],
            [
                "research_process_receipts." + x
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        CheckConstraint("stop_kind='stop'"),
        ForeignKeyConstraint(
            [*SCOPE, "admission_id", "extraction_receipt_id", "extraction_kind"],
            [
                "research_process_receipts." + x
                for x in (*SCOPE, "admission_id", "id", "kind")
            ],
        ),
        CheckConstraint("extraction_kind='extraction'"),
        CheckConstraint(
            "generation=1 AND result_sha256 ~ '^[a-f0-9]{64}$' AND launch_identity_sha256 ~ '^[a-f0-9]{64}$' AND stop_sha256 ~ '^[a-f0-9]{64}$' AND extraction_sha256 ~ '^[a-f0-9]{64}$' AND result_size_bytes BETWEEN 1 AND 16777216 AND octet_length(result_canonical)=result_size_bytes AND jsonb_typeof(result)='object' AND decision_count BETWEEN 1 AND 8"
        ),
    )


class StructuredDecision(Base):
    __tablename__ = "factor_strategy_decisions"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    observation_sha256: Mapped[str] = mapped_column(String(64))
    eligibility_sha256: Mapped[str] = mapped_column(String(64))
    score_sha256: Mapped[str] = mapped_column(String(64))
    allocation_sha256: Mapped[str] = mapped_column(String(64))
    reply_sha256: Mapped[str] = mapped_column(String(64))
    signal_id: Mapped[str] = mapped_column(String(64))
    record: Mapped[dict] = mapped_column(JSONB)
    record_canonical: Mapped[str] = mapped_column(Text)
    record_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("owner_id", "execution_id", "event_seq"),
        ForeignKeyConstraint(
            [*SCOPE, "execution_id"],
            [
                "factor_strategy_execution_outcomes." + x
                for x in (*SCOPE, "execution_id")
            ],
        ),
        CheckConstraint(
            "event_seq BETWEEN 0 AND 7 AND generation=1 AND octet_length(record_canonical) BETWEEN 1 AND 1048576 AND jsonb_typeof(record)='object' AND record_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class StructuredOrder(Base):
    __tablename__ = "factor_strategy_orders"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    native_order_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event_seq: Mapped[int] = mapped_column(Integer)
    logical_order_id: Mapped[str] = mapped_column(String(64))
    listing_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    record: Mapped[dict] = mapped_column(JSONB)
    record_canonical: Mapped[str] = mapped_column(Text)
    record_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("owner_id", "execution_id", "native_order_id"),
        ForeignKeyConstraint(
            ["owner_id", "execution_id", "event_seq"],
            [
                "factor_strategy_decisions.owner_id",
                "factor_strategy_decisions.execution_id",
                "factor_strategy_decisions.event_seq",
            ],
        ),
        CheckConstraint(
            "octet_length(record_canonical) BETWEEN 1 AND 65536 AND logical_order_id ~ '^[a-f0-9]{64}$' AND record_sha256 ~ '^[a-f0-9]{64}$' AND jsonb_typeof(record)='object'"
        ),
    )


class StructuredFill(Base):
    __tablename__ = "factor_strategy_fills"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    native_fill_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    native_order_id: Mapped[str] = mapped_column(String(200))
    record: Mapped[dict] = mapped_column(JSONB)
    record_canonical: Mapped[str] = mapped_column(Text)
    record_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "execution_id", "native_order_id"],
            [
                "factor_strategy_orders.owner_id",
                "factor_strategy_orders.execution_id",
                "factor_strategy_orders.native_order_id",
            ],
        ),
        CheckConstraint(
            "octet_length(record_canonical) BETWEEN 1 AND 65536 AND record_sha256 ~ '^[a-f0-9]{64}$' AND jsonb_typeof(record)='object'"
        ),
    )
