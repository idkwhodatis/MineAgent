"""Inactive RD domain evidence. Registration/activation is owned by migrations.

Importing this module does not enable a run purpose, process role or dispatcher.
All evidence rows require append-only triggers in the activation migration.
"""

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


class OwnedRunEvidence:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AttemptEvidence(OwnedRunEvidence):
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)


def run_fk():
    return ForeignKeyConstraint(
        ["owner_id", "run_id"], ["research_runs.owner_id", "research_runs.id"]
    )


def attempt_fk():
    return ForeignKeyConstraint(
        ["run_id", "generation", "attempt_id"],
        [
            "execution_attempts.run_id",
            "execution_attempts.generation",
            "execution_attempts.id",
        ],
    )


def exact_attempt_fk(column, table):
    names = ("owner_id", "run_id", "attempt_id", "generation")
    return ForeignKeyConstraint(
        [*names, column], [*(f"{table}.{n}" for n in names), f"{table}.id"]
    )


def scope_key():
    return UniqueConstraint("owner_id", "run_id", "attempt_id", "generation", "id")


class FactorResearchSpec(OwnedRunEvidence, Base):
    __tablename__ = "factor_research_specs"
    config: Mapped[dict] = mapped_column(JSONB)
    config_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        UniqueConstraint("run_id"),
        UniqueConstraint("owner_id", "run_id", "id"),
        CheckConstraint(
            "config_sha256 ~ '^[a-f0-9]{64}$' AND input_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(config) = 'object' AND config ? 'schema_version' AND jsonb_typeof(config->'schema_version') = 'string' AND config->>'schema_version' = 'mineagent.factor-research.v1'"
        ),
    )


class FactorGenerationStart(AttemptEvidence, Base):
    __tablename__ = "factor_generation_starts"
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_step: Mapped[int] = mapped_column(Integer)
    task_index: Mapped[int] = mapped_column(Integer)
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    mutation_reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_factor_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("mutation_reservation_id"),
        CheckConstraint(
            "logical_step=0 AND parent_factor_version_id IS NULL",
            name="ck_rd_source_initial_step",
        ),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"
        ),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "id", "context_id"
        ),
        UniqueConstraint("run_id", "generation", "logical_step", "task_index"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "spec_id"],
            [
                "factor_research_specs.owner_id",
                "factor_research_specs.run_id",
                "factor_research_specs.id",
            ],
        ),
        exact_attempt_fk("context_id", "research_operation_contexts"),
        exact_attempt_fk("mutation_reservation_id", "research_budget_reservations"),
        ForeignKeyConstraint(
            ["owner_id", "parent_factor_version_id"],
            ["factor_versions.owner_id", "factor_versions.id"],
        ),
        CheckConstraint(
            "generation > 0 AND logical_step BETWEEN 0 AND 7 AND task_index = 0"
        ),
    )


def start_context_fk():
    names = ("owner_id", "run_id", "attempt_id", "generation", "context_id")
    return ForeignKeyConstraint(
        [*names, "start_id"],
        [
            *("factor_generation_starts." + n for n in names),
            "factor_generation_starts.id",
        ],
    )


def call_scope_fk():
    names = ("owner_id", "run_id", "attempt_id", "generation", "start_id", "context_id")
    return ForeignKeyConstraint(
        [*names, "call_id"],
        [*("factor_upstream_calls." + n for n in names), "factor_upstream_calls.id"],
    )


class FactorUpstreamCall(AttemptEvidence, Base):
    """One actual upstream call, not one immutable broker request operation."""

    __tablename__ = "factor_upstream_calls"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(12))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        start_context_fk(),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "context_id",
            "id",
        ),
        UniqueConstraint("start_id", "phase", "ordinal"),
        CheckConstraint("generation > 0 AND ordinal BETWEEN 0 AND 999"),
        CheckConstraint(
            "phase IN ('generation','output_format','critique','final_decision','knowledge_query','knowledge_update')"
        ),
        CheckConstraint(
            "kind IN ('chat','embedding') AND (kind <> 'embedding' OR phase IN ('knowledge_query','knowledge_update'))"
        ),
    )


class FactorResponseAssembly(AttemptEvidence, Base):
    """Ordered response assembly plus exact parser/extraction evidence object."""

    __tablename__ = "factor_response_assemblies"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(
        String(40), default="factor_response_assembly"
    )
    object_sha256: Mapped[str] = mapped_column(String(64))
    part_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "call_id", "id"
        ),
        call_scope_fk(),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "context_id",
            "call_id",
            "id",
        ),
        UniqueConstraint("call_id"),
        ForeignKeyConstraint(
            ["owner_id", "object_id", "object_kind", "object_sha256"],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND part_count BETWEEN 1 AND 60 AND object_kind = 'factor_response_assembly' AND object_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorGenerationSource(AttemptEvidence, Base):
    __tablename__ = "factor_generation_sources"
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("factor_version_id"),
        UniqueConstraint("owner_id", "run_id", "id", "factor_version_id"),
        start_context_fk(),
        call_scope_fk(),
        UniqueConstraint("start_id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "factor_version_id",
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
                "assembly_id",
            ],
            [
                "factor_response_assemblies." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "start_id",
                    "context_id",
                    "call_id",
                    "id",
                )
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id", "source_sha256"],
            [
                "factor_versions.owner_id",
                "factor_versions.id",
                "factor_versions.code_sha256",
            ],
        ),
        CheckConstraint("generation > 0 AND source_sha256 ~ '^[a-f0-9]{64}$'"),
    )


class FactorGenerationOutcome(AttemptEvidence, Base):
    __tablename__ = "factor_generation_outcomes"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_step: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(30))
    factor_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    proposal_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    feedback_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    evaluated_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    adopted_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    adopted_factor_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    continuation_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    continuation_factor_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True)
    )
    adoption_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    adoption_object_kind: Mapped[str | None] = mapped_column(String(40))
    adoption_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id", "evaluated_source_id", "factor_version_id"],
            [
                "factor_generation_sources.owner_id",
                "factor_generation_sources.run_id",
                "factor_generation_sources.id",
                "factor_generation_sources.factor_version_id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "adopted_source_id", "adopted_factor_version_id"],
            [
                "factor_generation_sources.owner_id",
                "factor_generation_sources.run_id",
                "factor_generation_sources.id",
                "factor_generation_sources.factor_version_id",
            ],
        ),
        CheckConstraint(
            "(adopted_source_id IS NULL) = (adopted_factor_version_id IS NULL)"
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "continuation_source_id",
                "continuation_factor_version_id",
            ],
            [
                "factor_generation_sources.owner_id",
                "factor_generation_sources.run_id",
                "factor_generation_sources.id",
                "factor_generation_sources.factor_version_id",
            ],
        ),
        CheckConstraint(
            "(continuation_source_id IS NULL) = (continuation_factor_version_id IS NULL)"
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "adoption_object_id",
                "adoption_object_kind",
                "adoption_sha256",
            ],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint("(evaluated_source_id IS NULL) = (factor_version_id IS NULL)"),
        CheckConstraint(
            "((adoption_object_id IS NULL AND adoption_object_kind IS NULL AND adoption_sha256 IS NULL) OR (adoption_object_id IS NOT NULL AND adoption_object_kind = 'factor_adoption_receipt' AND adoption_sha256 ~ '^[a-f0-9]{64}$')) IS TRUE"
        ),
        CheckConstraint(
            "outcome NOT IN ('execution_failed','valid_unadopted','accepted') OR adoption_object_id IS NOT NULL"
        ),
        CheckConstraint("outcome <> 'accepted' OR adopted_source_id IS NOT NULL"),
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "logical_step",
            ],
            [
                "factor_generation_starts." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "logical_step",
                )
            ],
        ),
        UniqueConstraint("start_id"),
        exact_attempt_fk("start_id", "factor_generation_starts"),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id"],
            ["factor_versions.owner_id", "factor_versions.id"],
        ),
        ForeignKeyConstraint(
            ["owner_id", "proposal_object_id"],
            ["research_objects.owner_id", "research_objects.id"],
        ),
        ForeignKeyConstraint(
            ["owner_id", "feedback_object_id"],
            ["research_objects.owner_id", "research_objects.id"],
        ),
        CheckConstraint("generation > 0"),
        CheckConstraint(
            "outcome IN ('malformed_response','source_rejected','execution_failed','valid_unadopted','accepted','cancelled')"
        ),
        CheckConstraint(
            "outcome NOT IN ('execution_failed','valid_unadopted','accepted') OR factor_version_id IS NOT NULL"
        ),
        CheckConstraint(
            "outcome NOT IN ('malformed_response','source_rejected') OR factor_version_id IS NULL"
        ),
    )


class FactorGenerationModelOperation(AttemptEvidence, Base):
    __tablename__ = "factor_generation_model_operations"
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_ordinal: Mapped[int] = mapped_column(Integer)
    relation: Mapped[str] = mapped_column(String(20))
    predecessor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    protocol: Mapped[str] = mapped_column(String(40))
    parse_failure_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    parse_failure_object_kind: Mapped[str | None] = mapped_column(String(40))
    parse_failure_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "owner_id",
                "parse_failure_object_id",
                "parse_failure_object_kind",
                "parse_failure_sha256",
            ],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint(
            "protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1')"
        ),
        CheckConstraint(
            "((relation = 'semantic_repair' AND parse_failure_object_id IS NOT NULL AND parse_failure_object_kind = 'factor_parse_failure' AND parse_failure_sha256 ~ '^[a-f0-9]{64}$') OR (relation <> 'semantic_repair' AND parse_failure_object_id IS NULL AND parse_failure_object_kind IS NULL AND parse_failure_sha256 IS NULL)) IS TRUE"
        ),
        run_fk(),
        attempt_fk(),
        scope_key(),
        start_context_fk(),
        call_scope_fk(),
        UniqueConstraint("operation_id"),
        UniqueConstraint("call_id", "idempotency_key"),
        UniqueConstraint("call_id", "request_ordinal"),
        UniqueConstraint(
            "owner_id", "run_id", "attempt_id", "generation", "call_id", "id"
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "call_id",
                "predecessor_id",
            ],
            [
                "factor_generation_model_operations." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "call_id",
                    "id",
                )
            ],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "operation_id",
                "context_id",
            ],
            [
                "research_model_operations." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "context_id",
                )
            ],
        ),
        CheckConstraint("generation > 0 AND request_ordinal BETWEEN 0 AND 59"),
        CheckConstraint(
            "(request_ordinal = 0 AND relation = 'initial' AND predecessor_id IS NULL) OR (request_ordinal > 0 AND relation IN ('continuation','semantic_repair') AND predecessor_id IS NOT NULL AND predecessor_id <> id)"
        ),
    )


class FactorResponseAssemblyPart(AttemptEvidence, Base):
    __tablename__ = "factor_response_assembly_parts"
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    member_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_object_kind: Mapped[str] = mapped_column(
        String(40), default="model_response"
    )
    response_sha256: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "call_id",
                "assembly_id",
            ],
            [
                "factor_response_assemblies." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "call_id",
                    "id",
                )
            ],
        ),
        exact_attempt_fk("outcome_id", "research_model_outcomes"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "call_id", "member_id"],
            [
                "factor_generation_model_operations." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "call_id",
                    "id",
                )
            ],
        ),
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
        UniqueConstraint("assembly_id", "ordinal"),
        UniqueConstraint("assembly_id", "member_id"),
        CheckConstraint(
            "generation > 0 AND ordinal BETWEEN 0 AND 59 AND response_object_kind = 'model_response' AND response_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorEvaluationStart(AttemptEvidence, Base):
    """Durable logical evaluation ticket; repeated value reads reuse this ID."""

    __tablename__ = "factor_evaluation_starts"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evaluated_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    snapshot_input: Mapped[dict | None] = mapped_column(JSONB)
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    expected_cutoffs: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("start_id", "factor_generation_starts"),
        UniqueConstraint("start_id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "start_id",
            "evaluated_source_id",
            "factor_version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "evaluated_source_id", "factor_version_id"],
            [
                "factor_generation_sources.owner_id",
                "factor_generation_sources.run_id",
                "factor_generation_sources.id",
                "factor_generation_sources.factor_version_id",
            ],
        ),
        CheckConstraint("generation > 0 AND expected_cutoffs BETWEEN 0 AND 4096"),
        CheckConstraint(
            "((evaluated_source_id IS NULL AND factor_version_id IS NULL AND snapshot_input IS NULL AND input_sha256 IS NULL AND expected_cutoffs = 0) OR (evaluated_source_id IS NOT NULL AND factor_version_id IS NOT NULL AND jsonb_typeof(snapshot_input) = 'object' AND snapshot_input->>'schema_version' = 'mineagent.feature-input.v1' AND input_sha256 ~ '^[a-f0-9]{64}$' AND expected_cutoffs > 0)) IS TRUE"
        ),
    )


class FactorEvaluationOutcome(AttemptEvidence, Base):
    __tablename__ = "factor_evaluation_outcomes"
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24))
    snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    snapshot_input_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_output_sha256: Mapped[str | None] = mapped_column(String(64))
    feedback_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    feedback_object_kind: Mapped[str] = mapped_column(
        String(40), default="factor_generation_feedback"
    )
    feedback_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("evaluation_id", "factor_evaluation_starts"),
        UniqueConstraint("evaluation_id"),
        ForeignKeyConstraint(
            ["owner_id", "snapshot_id"],
            ["feature_snapshots.owner_id", "feature_snapshots.id"],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "feedback_object_id",
                "feedback_object_kind",
                "feedback_sha256",
            ],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND status IN ('valid','source_rejected','execution_failed','cancelled')"
        ),
        CheckConstraint(
            "feedback_object_kind = 'factor_generation_feedback' AND feedback_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "((status = 'valid' AND snapshot_id IS NOT NULL AND snapshot_input_sha256 ~ '^[a-f0-9]{64}$' AND snapshot_output_sha256 ~ '^[a-f0-9]{64}$') OR (status <> 'valid' AND snapshot_id IS NULL AND snapshot_input_sha256 IS NULL AND snapshot_output_sha256 IS NULL)) IS TRUE"
        ),
    )


class FactorCutoffExecution(AttemptEvidence, Base):
    __tablename__ = "factor_cutoff_executions"
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    cutoff_ordinal: Mapped[int] = mapped_column(Integer)
    projection: Mapped[dict] = mapped_column(JSONB)
    projection_sha256: Mapped[str] = mapped_column(String(64))
    candidate_admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evaluated_source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id", "evaluated_source_id", "factor_version_id"],
            [
                "factor_generation_sources.owner_id",
                "factor_generation_sources.run_id",
                "factor_generation_sources.id",
                "factor_generation_sources.factor_version_id",
            ],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "evaluation_id",
                "start_id",
                "evaluated_source_id",
                "factor_version_id",
            ],
            [
                "factor_evaluation_starts." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "start_id",
                    "evaluated_source_id",
                    "factor_version_id",
                )
            ],
        ),
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("start_id", "cutoff_ordinal"),
        UniqueConstraint("candidate_admission_id"),
        UniqueConstraint(
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "candidate_admission_id",
            "evaluated_source_id",
            name="uq_rd_cutoff_native_source",
        ),
        exact_attempt_fk("start_id", "factor_generation_starts"),
        exact_attempt_fk("candidate_admission_id", "research_process_admissions"),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id"],
            ["factor_versions.owner_id", "factor_versions.id"],
        ),
        CheckConstraint(
            "generation > 0 AND cutoff_ordinal BETWEEN 0 AND 4095 AND projection_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "(jsonb_typeof(projection) = 'object' AND projection ? 'schema_version' AND projection->>'schema_version' = 'mineagent.factor-prefix-input.v2') IS TRUE"
        ),
    )


class FactorCutoffOutcome(AttemptEvidence, Base):
    __tablename__ = "factor_cutoff_outcomes"
    execution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20))
    normalizer_admission_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    hdf_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    values_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    row_count: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    hdf_object_kind: Mapped[str | None] = mapped_column(String(40))
    hdf_sha256: Mapped[str | None] = mapped_column(String(64))
    values_object_kind: Mapped[str | None] = mapped_column(String(40))
    values_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("execution_id"),
        exact_attempt_fk("execution_id", "factor_cutoff_executions"),
        exact_attempt_fk("normalizer_admission_id", "research_process_admissions"),
        ForeignKeyConstraint(
            ["owner_id", "hdf_object_id", "hdf_object_kind", "hdf_sha256"],
            [
                "factor_objects.owner_id",
                "factor_objects.id",
                "factor_objects.object_kind",
                "factor_objects.sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "values_object_id", "values_object_kind", "values_sha256"],
            [
                "factor_objects.owner_id",
                "factor_objects.id",
                "factor_objects.object_kind",
                "factor_objects.sha256",
            ],
        ),
        CheckConstraint(
            "((hdf_object_id IS NULL AND hdf_object_kind IS NULL AND hdf_sha256 IS NULL) OR (hdf_object_id IS NOT NULL AND hdf_object_kind = 'native_hdf' AND hdf_sha256 ~ '^[a-f0-9]{64}$')) IS TRUE"
        ),
        CheckConstraint(
            "((values_object_id IS NULL AND values_object_kind IS NULL AND values_sha256 IS NULL) OR (values_object_id IS NOT NULL AND values_object_kind = 'values_partition' AND values_sha256 ~ '^[a-f0-9]{64}$')) IS TRUE"
        ),
        CheckConstraint(
            "generation > 0 AND row_count BETWEEN 0 AND 10000 AND status IN ('succeeded','failed','cancelled')"
        ),
        CheckConstraint(
            "status <> 'succeeded' OR (hdf_object_id IS NOT NULL AND values_object_id IS NOT NULL AND normalizer_admission_id IS NOT NULL AND error_code IS NULL)"
        ),
    )


class FactorResearchCheckpoint(AttemptEvidence, Base):
    __tablename__ = "factor_research_checkpoints"
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    completed_step: Mapped[int] = mapped_column(Integer)
    last_outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    state: Mapped[dict] = mapped_column(JSONB)
    state_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("owner_id", "run_id", "id"),
        UniqueConstraint("run_id", "completed_step"),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "spec_id"],
            [
                "factor_research_specs.owner_id",
                "factor_research_specs.run_id",
                "factor_research_specs.id",
            ],
        ),
        ForeignKeyConstraint(
            [
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "last_outcome_id",
                "completed_step",
            ],
            [
                "factor_generation_outcomes." + key
                for key in (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "logical_step",
                )
            ],
        ),
        CheckConstraint(
            "generation > 0 AND completed_step BETWEEN 0 AND 7 AND state_sha256 ~ '^[a-f0-9]{64}$'"
        ),
        CheckConstraint(
            "(jsonb_typeof(state) = 'object' AND state ? 'schema_version' AND state->>'schema_version' = 'mineagent.factor-research-checkpoint.v1') IS TRUE"
        ),
    )


class FactorResearchAttemptBinding(AttemptEvidence, Base):
    __tablename__ = "factor_research_attempt_bindings"
    spec_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    resumed_checkpoint_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        UniqueConstraint("attempt_id"),
        CheckConstraint(
            "resumed_checkpoint_id IS NULL", name="ck_rd_source_fresh_binding"
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
            ["owner_id", "run_id", "resumed_checkpoint_id"],
            [
                "factor_research_checkpoints.owner_id",
                "factor_research_checkpoints.run_id",
                "factor_research_checkpoints.id",
            ],
        ),
        CheckConstraint("generation > 0"),
    )


class FactorSourcePublication(AttemptEvidence, Base):
    """Durable pre-admission; an absent outcome keeps the full peak charged."""

    __tablename__ = "factor_source_publications"
    start_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    assembly_object_kind: Mapped[str] = mapped_column(String(40))
    assembly_sha256: Mapped[str] = mapped_column(String(64))
    budget_reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    peak_bytes: Mapped[int] = mapped_column(Integer)
    factor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("start_id", "factor_generation_starts"),
        exact_attempt_fk("call_id", "factor_upstream_calls"),
        exact_attempt_fk("budget_reservation_id", "research_budget_reservations"),
        UniqueConstraint("start_id"),
        UniqueConstraint("budget_reservation_id"),
        UniqueConstraint("factor_version_id"),
        ForeignKeyConstraint(
            [
                "owner_id",
                "assembly_object_id",
                "assembly_object_kind",
                "assembly_sha256",
            ],
            [
                "research_objects.owner_id",
                "research_objects.id",
                "research_objects.object_kind",
                "research_objects.sha256",
            ],
        ),
        CheckConstraint("generation > 0 AND size_bytes BETWEEN 1 AND 131072"),
        CheckConstraint("peak_bytes = size_bytes * 2 + 8192"),
        CheckConstraint(
            "assembly_object_kind = 'factor_response_assembly' AND assembly_sha256 ~ '^[a-f0-9]{64}$' AND source_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )


class FactorSourcePublicationOutcome(AttemptEvidence, Base):
    __tablename__ = "factor_source_publication_outcomes"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    journal_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("publication_id", "factor_source_publications"),
        exact_attempt_fk("source_id", "factor_generation_sources"),
        UniqueConstraint("publication_id"),
        UniqueConstraint("source_id"),
        CheckConstraint("generation > 0 AND journal_sha256 ~ '^[a-f0-9]{64}$'"),
    )


class FactorControllerLaunch(AttemptEvidence, Base):
    """One exact controller admission for an immutable fresh-attempt bootstrap."""

    __tablename__ = "factor_controller_launches"
    attempt_binding_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bootstrap: Mapped[dict] = mapped_column(JSONB)
    bootstrap_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("attempt_binding_id", "factor_research_attempt_bindings"),
        exact_attempt_fk("admission_id", "research_process_admissions"),
        UniqueConstraint("attempt_id"),
        UniqueConstraint("attempt_binding_id"),
        UniqueConstraint("admission_id"),
        CheckConstraint("generation > 0 AND bootstrap_sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint(
            "(jsonb_typeof(bootstrap) = 'object' AND bootstrap->>'schema_version' = 'mineagent.rdagent-operational-bootstrap.v1' AND octet_length(bootstrap::text) <= 262144) IS TRUE"
        ),
    )


class FactorUpstreamCallOutcome(AttemptEvidence, Base):
    """Authenticated exchange boundary, distinct from each request's transport."""

    __tablename__ = "factor_upstream_call_outcomes"
    call_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    controller_launch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20))
    member_count: Mapped[int] = mapped_column(Integer)
    member_ids: Mapped[list] = mapped_column(JSONB)
    response_outcome_ids: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        run_fk(),
        attempt_fk(),
        scope_key(),
        exact_attempt_fk("call_id", "factor_upstream_calls"),
        exact_attempt_fk("controller_launch_id", "factor_controller_launches"),
        UniqueConstraint("call_id"),
        CheckConstraint(
            "generation > 0 AND member_count BETWEEN 0 AND 60 AND status IN ('completed','aborted')"
        ),
        CheckConstraint("status <> 'completed' OR member_count > 0"),
        CheckConstraint(
            "(jsonb_typeof(member_ids) = 'array' AND jsonb_array_length(member_ids) = member_count AND jsonb_typeof(response_outcome_ids) = 'array' AND jsonb_array_length(response_outcome_ids) = member_count) IS TRUE"
        ),
    )


def _register_source_dependencies():
    # Called only after AttemptEvidence and all referenced tables are defined,
    # including when this module was imported before the common Base module.
    import importlib

    for name in (
        "rdagent_source_witness_models",
        "rdagent_boundary_models",
        "rdagent_input_models",
    ):
        importlib.import_module("backend.app." + name)


_register_source_dependencies()
