"""027 operator-backed JSON search graph, never the024 Python source graph.

Registered with the exact027 append-only/semantic/current-fence migration.
Importing model definitions alone never grants execution authority.
Same-snapshot children have independent execution IDs;025 acceptance-only
run/snapshot uniqueness and StrategySearchSpec Python FKs are not reused.
"""

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


def fk(local, table, remote):
    return ForeignKeyConstraint(local, [f"{table}.{key}" for key in remote])


def scoped_fk(field, table):
    return fk((*SCOPE, field), table, (*SCOPE, "id"))


def scope_constraints():
    return (
        UniqueConstraint(*SCOPE, "id"),
        fk(("owner_id", "run_id"), "oe_structured_specs", ("owner_id", "run_id")),
        fk(
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        CheckConstraint("generation=1"),
    )


class ScopedEvidence:
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class OEStructuredSubmission(Base):
    __tablename__ = "oe_structured_submissions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_canonical: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(
        String(40), default="authenticated_user_submission"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key"),
        UniqueConstraint("owner_id", "run_id", "id"),
        fk(("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        CheckConstraint(
            "origin IN ('authenticated_user_submission','workflow_stage') AND octet_length(idempotency_key) BETWEEN 1 AND 128 AND octet_length(request_canonical) BETWEEN 1 AND 65536 AND request_sha256 ~ '^[a-f0-9]{64}$' AND preflight_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class OEStructuredSpec(Base):
    __tablename__ = "oe_structured_specs"
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    submission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    request: Mapped[dict] = mapped_column(JSONB)
    request_canonical: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    preflight_sha256: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSONB)
    config_canonical: Mapped[str] = mapped_column(Text)
    config_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    seed_program_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id"),
        fk(
            ("owner_id", "run_id", "submission_id"),
            "oe_structured_submissions",
            ("owner_id", "run_id", "id"),
        ),
        fk(("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        fk(
            ("owner_id", "snapshot_id"),
            "factor_execution_snapshot_outcomes",
            ("owner_id", "publication_id"),
        ),
        fk(("owner_id", "dataset_id"), "dataset_versions", ("owner_id", "id")),
        fk(
            ("owner_id", "seed_program_version_id"),
            "structured_strategy_versions",
            ("owner_id", "id"),
        ),
        CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND preflight_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "octet_length(request_canonical) BETWEEN 1 AND 65536 AND octet_length(config_canonical) BETWEEN 1 AND 1048576 AND jsonb_typeof(request)='object' AND jsonb_typeof(config)='object'"
        ),
    )


class OEStructuredProviderExchange(ScopedEvidence, Base):
    """Completed SDK response linkage, not a caller-supplied score or proposal."""

    __tablename__ = "oe_structured_provider_exchanges"
    # Exact027 membership; no old024/RD transport is a substitute.
    provider_transport_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True
    )
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    iteration_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    parent_candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    context: Mapped[dict] = mapped_column(JSONB)
    context_canonical: Mapped[str] = mapped_column(Text)
    context_sha256: Mapped[str] = mapped_column(String(64))
    request_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_kind: Mapped[str] = mapped_column(
        String(40), default="oe_structured_provider_request"
    )
    request_sha256: Mapped[str] = mapped_column(String(64))
    response_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_kind: Mapped[str] = mapped_column(
        String(40), default="oe_structured_provider_response"
    )
    response_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        fk((*SCOPE, "epoch_id"), "oe_structured_epochs", (*SCOPE, "id")),
        fk(
            (*SCOPE, "iteration_intent_id"),
            "oe_structured_iteration_intents",
            (*SCOPE, "id"),
        ),
        fk(
            (*SCOPE, "provider_transport_id"),
            "oe_structured_provider_transports",
            (*SCOPE, "id"),
        ),
        fk((*SCOPE, "parent_candidate_id"), "oe_structured_candidates", (*SCOPE, "id")),
        fk(
            ("owner_id", "request_object_id", "request_kind", "request_sha256"),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        fk(
            ("owner_id", "response_object_id", "response_kind", "response_sha256"),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        CheckConstraint(
            "request_kind='oe_structured_provider_request' AND response_kind='oe_structured_provider_response'"
        ),
        CheckConstraint(
            "context_sha256 ~ '^[a-f0-9]{64}$' AND request_sha256 ~ '^[a-f0-9]{64}$' AND response_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(context_canonical) BETWEEN 1 AND 65536 AND jsonb_typeof(context)='object'"
        ),
    )


class OEStructuredCandidate(ScopedEvidence, Base):
    __tablename__ = "oe_structured_candidates"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upstream_program_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    provider_exchange_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    program_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_canonical: Mapped[str] = mapped_column(Text)
    candidate_sha256: Mapped[str] = mapped_column(String(64))
    program_sha256: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        *scope_constraints(),
        fk((*SCOPE, "epoch_id"), "oe_structured_epochs", (*SCOPE, "id")),
        UniqueConstraint("run_id", "ordinal"),
        UniqueConstraint("epoch_id", "upstream_program_id"),
        UniqueConstraint("provider_exchange_id"),
        fk((*SCOPE, "parent_candidate_id"), "oe_structured_candidates", (*SCOPE, "id")),
        fk(
            (*SCOPE, "provider_exchange_id"),
            "oe_structured_provider_exchanges",
            (*SCOPE, "id"),
        ),
        fk(
            ("owner_id", "program_version_id"),
            "structured_strategy_versions",
            ("owner_id", "id"),
        ),
        CheckConstraint(
            "ordinal BETWEEN 0 AND 2 AND candidate_sha256 ~ '^[a-f0-9]{64}$' AND program_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(candidate_canonical) BETWEEN 1 AND 32768"
        ),
        CheckConstraint(
            "(ordinal=0 AND origin IN ('authenticated_user_seed','workflow_seed') AND parent_candidate_id IS NULL AND provider_exchange_id IS NULL) OR (ordinal>0 AND origin='model_proposal' AND parent_candidate_id IS NOT NULL AND provider_exchange_id IS NOT NULL AND parent_candidate_id<>id)"
        ),
    )


class OEStructuredExecution(ScopedEvidence, Base):
    __tablename__ = "oe_structured_executions"
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    program_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    strategy_input: Mapped[dict] = mapped_column(JSONB)
    strategy_input_canonical: Mapped[str] = mapped_column(Text)
    strategy_input_sha256: Mapped[str] = mapped_column(String(64))
    strategy_input_size_bytes: Mapped[int] = mapped_column(BigInteger)
    market_sha256: Mapped[str] = mapped_column(String(64))
    market_size_bytes: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        *scope_constraints(),
        fk((*SCOPE, "candidate_id"), "oe_structured_candidates", (*SCOPE, "id")),
        fk(
            ("owner_id", "program_version_id"),
            "structured_strategy_versions",
            ("owner_id", "id"),
        ),
        fk(
            ("owner_id", "snapshot_id"),
            "factor_execution_snapshot_outcomes",
            ("owner_id", "publication_id"),
        ),
        CheckConstraint(
            "strategy_input_sha256 ~ '^[a-f0-9]{64}$' AND market_sha256 ~ '^[a-f0-9]{64}$' AND strategy_input_size_bytes BETWEEN 1 AND 1048576 AND octet_length(strategy_input_canonical)=strategy_input_size_bytes AND jsonb_typeof(strategy_input)='object' AND market_size_bytes BETWEEN 1 AND 33554432"
        ),
    )


class OEStructuredEngineOutcome(ScopedEvidence, Base):
    """One actual stopped engine result and recomputed declared objective.

    Raw canonical float bytes are retained, not reconstructed from PostgreSQL
    JSONB. There is no successful row for an unknown/failed physical execution.
    """

    __tablename__ = "oe_structured_engine_outcomes"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_receipt_kind: Mapped[str] = mapped_column(String(30), default="stop")
    stop_receipt_sha256: Mapped[str] = mapped_column(String(64))
    extraction_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    extraction_receipt_kind: Mapped[str] = mapped_column(
        String(30), default="extraction"
    )
    extraction_receipt_sha256: Mapped[str] = mapped_column(String(64))
    result_canonical: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str] = mapped_column(String(64))
    result_size_bytes: Mapped[int] = mapped_column(BigInteger)
    measurement_canonical: Mapped[str] = mapped_column(Text)
    measurement_sha256: Mapped[str] = mapped_column(String(64))
    objective_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope_constraints(),
        UniqueConstraint(*SCOPE, "execution_id", "candidate_id", "id"),
        fk((*SCOPE, "execution_id"), "oe_structured_executions", (*SCOPE, "id")),
        fk((*SCOPE, "candidate_id"), "oe_structured_candidates", (*SCOPE, "id")),
        fk((*SCOPE, "admission_id"), "research_process_admissions", (*SCOPE, "id")),
        fk(
            (*SCOPE, "admission_id", "stop_receipt_id", "stop_receipt_kind"),
            "research_process_receipts",
            (*SCOPE, "admission_id", "id", "kind"),
        ),
        fk(
            (
                *SCOPE,
                "admission_id",
                "extraction_receipt_id",
                "extraction_receipt_kind",
            ),
            "research_process_receipts",
            (*SCOPE, "admission_id", "id", "kind"),
        ),
        CheckConstraint(
            "stop_receipt_kind='stop' AND extraction_receipt_kind='extraction'"
        ),
        CheckConstraint(
            "stop_receipt_sha256 ~ '^[a-f0-9]{64}$' AND extraction_receipt_sha256 ~ '^[a-f0-9]{64}$' AND result_sha256 ~ '^[a-f0-9]{64}$' AND measurement_sha256 ~ '^[a-f0-9]{64}$' AND objective_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "result_size_bytes BETWEEN 1 AND 16777216 AND octet_length(result_canonical)=result_size_bytes AND octet_length(measurement_canonical) BETWEEN 1 AND 65536"
        ),
    )


# isort: split
# Import dependents only after the shared mixin/helpers exist. This also makes
# fresh direct service imports safe when they enter through this module first.
from backend.app import oe_structured_checkpoint_models as _checkpoint_models  # noqa: F401
from backend.app import oe_structured_operational_models as _operational_models  # noqa: F401
from backend.app import oe_structured_sampling_models as _sampling_models  # noqa: F401
from backend.app import oe_structured_terminal_models as _terminal_models  # noqa: F401
