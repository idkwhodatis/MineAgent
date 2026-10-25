"""Neutral append-only accounting evidence and narrowly mutable projections."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class Record:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class BoundRecord(Record):
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)


def run_fk():
    return ForeignKeyConstraint(
        ["owner_id", "run_id"], ["research_runs.owner_id", "research_runs.id"]
    )


def bound_constraints():
    return (
        run_fk(),
        ForeignKeyConstraint(
            ["run_id", "generation", "attempt_id"],
            [
                "execution_attempts.run_id",
                "execution_attempts.generation",
                "execution_attempts.id",
            ],
        ),
        UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id"),
        CheckConstraint("generation > 0"),
    )


def bound_fk(column, table):
    return ForeignKeyConstraint(
        ["owner_id", "run_id", "attempt_id", "generation", column],
        [
            f"{table}.owner_id",
            f"{table}.run_id",
            f"{table}.attempt_id",
            f"{table}.generation",
            f"{table}.id",
        ],
    )


def object_fk(prefix):
    return ForeignKeyConstraint(
        [
            "owner_id",
            f"{prefix}_object_id",
            f"{prefix}_object_kind",
            f"{prefix}_sha256",
        ],
        [
            "research_objects.owner_id",
            "research_objects.id",
            "research_objects.object_kind",
            "research_objects.sha256",
        ],
    )


class ResearchBudgetControl(Record, Base):
    __tablename__ = "research_budget_controls"
    limits: Mapped[dict] = mapped_column(JSONB)
    used: Mapped[dict] = mapped_column(JSONB)
    reserved: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        run_fk(),
        UniqueConstraint("owner_id", "run_id"),
        CheckConstraint(
            "mineagent_budget_vector(limits) AND mineagent_budget_vector(used) AND mineagent_budget_vector(reserved)",
            name="ck_neutral_budget_vectors",
        ),
    )


class ResearchBudgetReservation(BoundRecord, Base):
    __tablename__ = "research_budget_reservations"
    purpose: Mapped[str] = mapped_column(String(32))
    logical_key: Mapped[str] = mapped_column(String(128))
    amounts: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    __table_args__ = (
        *bound_constraints(),
        ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["research_budget_controls.owner_id", "research_budget_controls.run_id"],
        ),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "purpose", "logical_key"
        ),
        CheckConstraint(
            "purpose IN ('model_transport','process_unit','object_storage')"
        ),
        CheckConstraint("state IN ('reserved','uncertain','settled')"),
        CheckConstraint(
            "mineagent_budget_vector(amounts)", name="ck_neutral_reservation_vector"
        ),
    )


class ResearchBudgetSettlement(BoundRecord, Base):
    __tablename__ = "research_budget_settlements"
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    charge: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        *bound_constraints(),
        bound_fk("reservation_id", "research_budget_reservations"),
        CheckConstraint(
            "outcome IN ('measured','not_submitted','conservative_unknown')"
        ),
        CheckConstraint(
            "mineagent_budget_vector(charge)", name="ck_neutral_settlement_vector"
        ),
    )


class ResearchOperationContext(BoundRecord, Base):
    __tablename__ = "research_operation_contexts"
    context: Mapped[dict] = mapped_column(JSONB)
    context_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *bound_constraints(),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "context_sha256"
        ),
        CheckConstraint("context_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint(
            "(jsonb_typeof(context) = 'object' AND context ?& ARRAY['schema_version','scenario','ordinal'] AND context - ARRAY['schema_version','scenario','ordinal'] = '{}'::jsonb AND context->>'schema_version' = 'mineagent.neutral-fixture-context.v1' AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal') = 'number' AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE"
        ),
    )


class ResearchContextAbort(BoundRecord, Base):
    """Immutable revocation: never a refund or permission for a replacement call."""

    __tablename__ = "research_context_aborts"
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    reason: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        *bound_constraints(),
        bound_fk("context_id", "research_operation_contexts"),
        CheckConstraint(
            "reason IN ('cancelled','budget_exhausted','publication_failed','request_drift','uncertain_transport')"
        ),
    )


class ResearchModelOperation(BoundRecord, Base):
    __tablename__ = "research_model_operations"
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    request: Mapped[dict] = mapped_column(JSONB)
    request_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_object_kind: Mapped[str] = mapped_column(
        String(32), default="model_request"
    )
    request_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="open")
    __table_args__ = (
        *bound_constraints(),
        bound_fk("context_id", "research_operation_contexts"),
        object_fk("request"),
        UniqueConstraint("context_id", "purpose", "ordinal"),
        CheckConstraint("purpose IN ('chat','embedding','continuation')"),
        CheckConstraint(
            "((purpose = 'embedding') = (request->>'kind' = 'embedding')) IS TRUE",
            name="ck_neutral_operation_request_purpose",
        ),
        CheckConstraint("ordinal >= 0 AND ordinal <= 1000000"),
        CheckConstraint("request_object_kind = 'model_request'"),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("state IN ('open','completed','unknown','aborted')"),
        CheckConstraint("mineagent_broker_request(request) IS TRUE"),
    )


class ResearchModelTransport(BoundRecord, Base):
    __tablename__ = "research_model_transports"
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    __table_args__ = (
        *bound_constraints(),
        bound_fk("operation_id", "research_model_operations"),
        bound_fk("reservation_id", "research_budget_reservations"),
        UniqueConstraint("operation_id", "ordinal"),
        CheckConstraint("ordinal > 0"),
        CheckConstraint(
            "state IN ('reserved','submitted','completed','failed','unknown')"
        ),
    )


class ResearchModelOutcome(BoundRecord, Base):
    __tablename__ = "research_model_outcomes"
    transport_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    outcome: Mapped[str] = mapped_column(String(20))
    usage: Mapped[dict] = mapped_column(JSONB)
    response_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    response_object_kind: Mapped[str | None] = mapped_column(String(32))
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        *bound_constraints(),
        bound_fk("transport_id", "research_model_transports"),
        object_fk("response"),
        CheckConstraint("outcome IN ('completed','failed','unknown')"),
        CheckConstraint(
            "mineagent_budget_vector(usage)", name="ck_neutral_outcome_vector"
        ),
        CheckConstraint(
            "((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE"
        ),
    )


BROKER_TABLES = (
    ResearchBudgetControl,
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
    ResearchOperationContext,
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchModelTransport,
    ResearchModelOutcome,
)

# Installed by the integrator-owned migration before creating broker tables.
BROKER_REQUEST_SQL = r"""
CREATE FUNCTION mineagent_broker_request(v jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
SELECT COALESCE(CASE WHEN jsonb_typeof(v) <> 'object' OR v IS NULL THEN false
WHEN v->>'kind' = 'chat' THEN
 v ?& ARRAY['kind','model','messages','max_output_tokens','temperature_millionths']
 AND v - ARRAY['kind','model','messages','max_output_tokens','temperature_millionths'] = '{}'::jsonb
 AND v->>'model' = 'neutral-fixture-v1'
 AND jsonb_typeof(v->'max_output_tokens') = 'number'
 AND v->>'max_output_tokens' ~ '^[0-9]+$'
 AND (v->>'max_output_tokens')::numeric BETWEEN 1 AND 4096
 AND jsonb_typeof(v->'temperature_millionths') = 'number'
 AND v->>'temperature_millionths' ~ '^[0-9]+$'
 AND (v->>'temperature_millionths')::numeric BETWEEN 0 AND 1000000
 AND CASE WHEN jsonb_typeof(v->'messages') = 'array' THEN
 jsonb_array_length(v->'messages') BETWEEN 1 AND 16
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v->'messages') m
 WHERE (jsonb_typeof(m) = 'object' AND m ?& ARRAY['role','content']
 AND m - ARRAY['role','content'] = '{}'::jsonb
 AND m->>'role' IN ('system','user','assistant')
 AND jsonb_typeof(m->'content') = 'string'
 AND length(m->>'content') BETWEEN 1 AND 8192) IS NOT TRUE)
 ELSE false END
WHEN v->>'kind' = 'embedding' THEN
 v ?& ARRAY['kind','model','inputs','dimensions']
 AND v - ARRAY['kind','model','inputs','dimensions'] = '{}'::jsonb
 AND v->>'model' = 'neutral-fixture-v1'
 AND jsonb_typeof(v->'dimensions') = 'number' AND v->>'dimensions' ~ '^[0-9]+$'
 AND (v->>'dimensions')::numeric BETWEEN 1 AND 64
 AND CASE WHEN jsonb_typeof(v->'inputs') = 'array' THEN
 jsonb_array_length(v->'inputs') BETWEEN 1 AND 16
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v->'inputs') item
 WHERE (jsonb_typeof(item) = 'string' AND length(item #>> '{}') BETWEEN 1 AND 8192) IS NOT TRUE)
 ELSE false END
ELSE false END, false)
$$;
"""
