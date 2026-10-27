"""Draft closed search-domain persistence, deliberately not registered or migrated.

Activation requires additive source tuple uniqueness, append-only triggers,
complete object/backup closure, and engine-specific lifecycle admission.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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
    return ForeignKeyConstraint(local, [f"{table}.{field}" for field in remote])


def scope_constraints():
    return (
        UniqueConstraint(*SCOPE, "id"),
        fk(["owner_id", "run_id"], "strategy_search_specs", ["owner_id", "run_id"]),
        fk(
            ["run_id", "generation", "attempt_id"],
            "execution_attempts",
            ["run_id", "generation", "id"],
        ),
        CheckConstraint("generation > 0"),
    )


class AttemptEvidence:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class StrategySearchSpec(Base):
    __tablename__ = "strategy_search_specs"
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    spec_sha256: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict] = mapped_column(JSONB)
    original_strategy_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    original_source_sha256: Mapped[str] = mapped_column(String(64))
    prepared_strategy_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    prepared_source_sha256: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    objective_sha256: Mapped[str] = mapped_column(String(64))
    execution_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id"),
        UniqueConstraint("owner_id", "run_id", "spec_sha256"),
        UniqueConstraint("owner_id", "run_id", "execution_sha256"),
        UniqueConstraint("owner_id", "run_id", "objective_sha256"),
        UniqueConstraint(
            "owner_id", "run_id", "original_strategy_id", "original_source_sha256"
        ),
        UniqueConstraint(
            "owner_id", "run_id", "prepared_strategy_id", "prepared_source_sha256"
        ),
        fk(["owner_id", "run_id"], "research_runs", ["owner_id", "id"]),
        fk(
            ["owner_id", "original_strategy_id", "original_source_sha256"],
            "strategy_versions",
            ["owner_id", "id", "content_sha256"],
        ),
        fk(
            ["owner_id", "prepared_strategy_id", "prepared_source_sha256"],
            "strategy_versions",
            ["owner_id", "id", "content_sha256"],
        ),
        fk(
            ["owner_id", "dataset_id", "dataset_sha256"],
            "dataset_versions",
            ["owner_id", "id", "content_sha256"],
        ),
        CheckConstraint(
            "spec_sha256 ~ '^[0-9a-f]{64}$' AND objective_sha256 ~ '^[0-9a-f]{64}$' AND execution_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(spec) = 'object' AND spec ? 'schema_version' AND jsonb_typeof(spec->'schema_version') = 'string' AND spec->>'schema_version' = 'mineagent.strategy-search.v1'"
        ),
    )


class StrategySearchEpoch(AttemptEvidence, Base):
    __tablename__ = "strategy_search_epochs"
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bootstrap_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_sha256: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    spec_sha256: Mapped[str] = mapped_column(String(64))
    first_native_iteration: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(24))
    # Checkpoint selection is added only with complete native checkpoint closure.
    __table_args__ = scope_constraints() + (
        UniqueConstraint("admission_id"),
        fk([*SCOPE, "admission_id"], "research_process_admissions", [*SCOPE, "id"]),
        CheckConstraint(
            "bootstrap_sha256 ~ '^[0-9a-f]{64}$' AND runtime_profile_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        UniqueConstraint("run_id", "ordinal"),
        fk(
            ["owner_id", "run_id", "spec_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "spec_sha256"],
        ),
        CheckConstraint(
            "ordinal = 1 AND first_native_iteration = 1 AND reason = 'initial'"
        ),
    )


class SearchCandidate(AttemptEvidence, Base):
    __tablename__ = "search_candidates"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    upstream_program_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(String(40), default="search_source")
    source_object_sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    seed_strategy_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    parent_candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    parent_source_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint(*SCOPE, "epoch_id", "id", "source_sha256"),
        UniqueConstraint("run_id", "epoch_id", "upstream_program_id"),
        fk([*SCOPE, "epoch_id"], "strategy_search_epochs", [*SCOPE, "id"]),
        fk(
            ["owner_id", "source_object_id", "source_kind", "source_object_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        fk(
            ["owner_id", "seed_strategy_id", "source_sha256"],
            "strategy_versions",
            ["owner_id", "id", "content_sha256"],
        ),
        fk(
            [*SCOPE, "epoch_id", "parent_candidate_id", "parent_source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        CheckConstraint(
            "source_kind = 'search_source' AND source_object_sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "(kind='seed' AND seed_strategy_id IS NOT NULL AND parent_candidate_id IS NULL AND parent_source_sha256 IS NULL) OR (kind='mutation' AND seed_strategy_id IS NULL AND parent_candidate_id IS NOT NULL AND parent_source_sha256 IS NOT NULL)"
        ),
        fk(
            ["owner_id", "run_id", "seed_strategy_id", "source_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "prepared_strategy_id", "prepared_source_sha256"],
        ),
        CheckConstraint("parent_candidate_id IS NULL OR parent_candidate_id <> id"),
    )


class SearchIterationIntent(AttemptEvidence, Base):
    __tablename__ = "search_iteration_intents"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    native_iteration: Mapped[int] = mapped_column(Integer)
    mutation_slot: Mapped[int] = mapped_column(Integer)
    parent_candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    parent_source_sha256: Mapped[str] = mapped_column(String(64))
    mutation_reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    selection_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    selection_kind: Mapped[str] = mapped_column(String(40), default="search_context")
    selection_sha256: Mapped[str] = mapped_column(String(64))
    target_island: Mapped[int] = mapped_column(Integer)
    __table_args__ = scope_constraints() + (
        UniqueConstraint(*SCOPE, "epoch_id", "id"),
        UniqueConstraint(*SCOPE, "epoch_id", "id", "native_iteration"),
        UniqueConstraint("run_id", "epoch_id", "native_iteration"),
        UniqueConstraint("run_id", "mutation_slot"),
        UniqueConstraint("mutation_reservation_id"),
        fk(
            [*SCOPE, "epoch_id", "parent_candidate_id", "parent_source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        fk(
            [*SCOPE, "mutation_reservation_id"],
            "research_budget_reservations",
            [*SCOPE, "id"],
        ),
        fk(
            ["owner_id", "selection_object_id", "selection_kind", "selection_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "native_iteration > 0 AND mutation_slot > 0 AND target_island = 0"
        ),
        CheckConstraint(
            "selection_kind = 'search_context' AND selection_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchEvaluation(AttemptEvidence, Base):
    __tablename__ = "search_evaluations"
    epoch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    original_strategy_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    prepared_strategy_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(32))
    source_sha256: Mapped[str] = mapped_column(String(64))
    window_id: Mapped[str] = mapped_column(String(48))
    execution_sha256: Mapped[str] = mapped_column(String(64))
    input_manifest_sha256: Mapped[str] = mapped_column(String(64))
    logical_ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    physical_ordinal: Mapped[int] = mapped_column(Integer)
    reservation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    process_admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = scope_constraints() + (
        UniqueConstraint(
            *SCOPE,
            "epoch_id",
            "candidate_id",
            "source_sha256",
            "id",
            "window_id",
            "execution_sha256",
        ),
        UniqueConstraint(
            *SCOPE, "id", "source_sha256", "window_id", "execution_sha256"
        ),
        UniqueConstraint(*SCOPE, "id", "process_admission_id"),
        UniqueConstraint("logical_ticket_id", "physical_ordinal"),
        fk(
            ["owner_id", "run_id", "original_strategy_id", "source_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "original_strategy_id", "original_source_sha256"],
        ),
        fk(
            ["owner_id", "run_id", "prepared_strategy_id", "source_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "prepared_strategy_id", "prepared_source_sha256"],
        ),
        fk(
            ["owner_id", "run_id", "execution_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "execution_sha256"],
        ),
        CheckConstraint(
            "(phase='seed_original_parity' AND original_strategy_id IS NOT NULL AND prepared_strategy_id IS NULL AND epoch_id IS NULL AND candidate_id IS NULL) OR (phase='seed_prepared_parity' AND prepared_strategy_id IS NOT NULL AND original_strategy_id IS NULL AND epoch_id IS NULL AND candidate_id IS NULL) OR (phase IN ('seed','mutation') AND original_strategy_id IS NULL AND prepared_strategy_id IS NULL AND epoch_id IS NOT NULL AND candidate_id IS NOT NULL)"
        ),
        UniqueConstraint("reservation_id"),
        UniqueConstraint("process_admission_id"),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        fk([*SCOPE, "reservation_id"], "research_budget_reservations", [*SCOPE, "id"]),
        fk(
            [*SCOPE, "process_admission_id"],
            "research_process_admissions",
            [*SCOPE, "id"],
        ),
        CheckConstraint(
            "physical_ordinal > 0 AND execution_sha256 ~ '^[0-9a-f]{64}$' AND input_manifest_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchObjectiveReceipt(AttemptEvidence, Base):
    __tablename__ = "search_objective_receipts"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    objective_sha256: Mapped[str] = mapped_column(String(64))
    evidence_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evidence_kind: Mapped[str] = mapped_column(String(40), default="search_objective")
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    combined_score: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[str] = mapped_column(String(16384))
    feedback_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        CheckConstraint(
            "octet_length(feedback)<=16384 AND feedback_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        UniqueConstraint("run_id", "epoch_id", "candidate_id", "objective_sha256"),
        fk(
            ["owner_id", "run_id", "objective_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "objective_sha256"],
        ),
        UniqueConstraint(*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        fk(
            ["owner_id", "evidence_object_id", "evidence_kind", "evidence_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "evidence_kind = 'search_objective' AND objective_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "(status='eligible' AND combined_score IS NOT NULL AND combined_score NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)) OR (status IN ('undefined_objective','execution_failed','constraint_failed') AND combined_score IS NULL)"
        ),
    )


class SearchObjectiveEvaluation(AttemptEvidence, Base):
    """One exact physical observation per declared objective window."""

    __tablename__ = "search_objective_evaluations"
    objective_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    window_id: Mapped[str] = mapped_column(String(48))
    execution_sha256: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = scope_constraints() + (
        UniqueConstraint("objective_receipt_id", "window_id"),
        UniqueConstraint("objective_receipt_id", "ordinal"),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "candidate_id",
                "source_sha256",
                "objective_receipt_id",
            ],
            "search_objective_receipts",
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"],
        ),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "candidate_id",
                "source_sha256",
                "evaluation_id",
                "window_id",
                "execution_sha256",
            ],
            "search_evaluations",
            [
                *SCOPE,
                "epoch_id",
                "candidate_id",
                "source_sha256",
                "id",
                "window_id",
                "execution_sha256",
            ],
        ),
        fk(
            [*SCOPE, "evaluation_id", "outcome_id"],
            "search_evaluation_outcomes",
            [*SCOPE, "evaluation_id", "id"],
        ),
        CheckConstraint("ordinal BETWEEN 0 AND 7"),
    )


class SearchResolvedExecutionProfile(Base):
    """Full immutable execution values; not a new public submit body."""

    __tablename__ = "search_resolved_execution_profiles"
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    execution_sha256: Mapped[str] = mapped_column(String(64))
    profile_sha256: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict] = mapped_column(JSONB)
    profile_canonical: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("owner_id", "run_id", "profile_sha256"),
        fk(
            ["owner_id", "run_id", "execution_sha256"],
            "strategy_search_specs",
            ["owner_id", "run_id", "execution_sha256"],
        ),
        CheckConstraint(
            "profile_sha256 ~ '^[0-9a-f]{64}$' AND jsonb_typeof(profile)='object' AND profile->>'schema_version'='mineagent.resolved-native-inputs.v1' AND octet_length(profile::text)<=65536"
        ),
        CheckConstraint(
            "octet_length(profile_canonical) BETWEEN 2 AND 65536 AND profile_canonical::jsonb=profile AND encode(sha256(convert_to(profile_canonical,'UTF8')),'hex')=profile_sha256"
        ),
    )


class SearchCandidateGrant(AttemptEvidence, Base):
    __tablename__ = "search_candidate_grants"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    intent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    proposal_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    proposal_stage: Mapped[str | None] = mapped_column(String(16))
    proposal_evidence: Mapped[str | None] = mapped_column(String(32))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(16))
    ticket_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("candidate_id"),
        UniqueConstraint("ticket_sha256"),
        UniqueConstraint("intent_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256"],
            "search_candidates",
            [*SCOPE, "epoch_id", "id", "source_sha256"],
        ),
        fk(
            [*SCOPE, "epoch_id", "intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk([*SCOPE, "admission_id"], "research_process_admissions", [*SCOPE, "id"]),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "intent_id",
                "source_sha256",
                "proposal_source_id",
                "proposal_stage",
                "proposal_evidence",
            ],
            "search_proposal_sources",
            [
                *SCOPE,
                "epoch_id",
                "intent_id",
                "source_sha256",
                "id",
                "stage",
                "evidence",
            ],
        ),
        CheckConstraint(
            "(phase='seed' AND intent_id IS NULL AND proposal_source_id IS NULL AND proposal_stage IS NULL AND proposal_evidence IS NULL) OR (phase='mutation' AND intent_id IS NOT NULL AND proposal_source_id IS NOT NULL AND proposal_stage IS NOT NULL AND proposal_evidence IS NOT NULL AND proposal_stage='enforced' AND proposal_evidence='complete_source')"
        ),
        CheckConstraint(
            "ticket_sha256 ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchRenderedContext(AttemptEvidence, Base):
    __tablename__ = "search_rendered_contexts"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rendered_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rendered_kind: Mapped[str] = mapped_column(
        String(40), default="search_rendered_prompt"
    )
    rendered_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint(*SCOPE, "epoch_id", "intent_id", "id"),
        UniqueConstraint("intent_id"),
        UniqueConstraint("context_id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk([*SCOPE, "context_id"], "research_operation_contexts", [*SCOPE, "id"]),
        fk(
            ["owner_id", "rendered_object_id", "rendered_kind", "rendered_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "rendered_kind='search_rendered_prompt' AND rendered_sha256 ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchEvaluationOutcome(AttemptEvidence, Base):
    """A physical attempt outcome is distinct from its logical objective."""

    __tablename__ = "search_evaluation_outcomes"
    evaluation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    admission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stop_kind: Mapped[str] = mapped_column(String(30), default="stop")
    status: Mapped[str] = mapped_column(String(24))
    bundle_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    bundle_kind: Mapped[str] = mapped_column(String(40))
    bundle_sha256: Mapped[str] = mapped_column(String(64))
    native_bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    input_manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("evaluation_id"),
        UniqueConstraint(*SCOPE, "evaluation_id", "id"),
        fk(
            [*SCOPE, "evaluation_id", "admission_id"],
            "search_evaluations",
            [*SCOPE, "id", "process_admission_id"],
        ),
        fk(
            [*SCOPE, "admission_id", "stop_receipt_id", "stop_kind"],
            "research_process_receipts",
            [*SCOPE, "admission_id", "id", "kind"],
        ),
        fk(
            ["owner_id", "bundle_object_id", "bundle_kind", "bundle_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "stop_kind='stop' AND ((status='observed' AND bundle_kind='search_native_observation' AND native_bundle_sha256 IS NOT NULL AND native_bundle_sha256 ~ '^[0-9a-f]{64}$') OR (status='failed' AND bundle_kind='search_native_failure' AND native_bundle_sha256 IS NULL))"
        ),
        CheckConstraint(
            "bundle_sha256 ~ '^[0-9a-f]{64}$' AND input_manifest_sha256 ~ '^[0-9a-f]{64}$' AND request_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchObjectiveAcknowledgement(AttemptEvidence, Base):
    __tablename__ = "search_objective_acknowledgements"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str] = mapped_column(String(64))
    objective_receipt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    grant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("objective_receipt_id"),
        UniqueConstraint("grant_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "candidate_id",
                "source_sha256",
                "objective_receipt_id",
            ],
            "search_objective_receipts",
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "grant_id"],
            "search_candidate_grants",
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"],
        ),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
    )


class SearchWorkerOutcome(AttemptEvidence, Base):
    __tablename__ = "search_worker_outcomes"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    native_iteration: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    objective_ack_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    evidence_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    evidence_kind: Mapped[str] = mapped_column(
        String(40), default="search_worker_outcome"
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "native_iteration", "id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "native_iteration"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id", "native_iteration"],
        ),
        fk(
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "objective_ack_id"],
            "search_objective_acknowledgements",
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"],
        ),
        CheckConstraint(
            "status IN ('evaluated','worker_error') AND native_iteration>0"
        ),
        CheckConstraint(
            "(candidate_id IS NULL AND source_sha256 IS NULL AND objective_ack_id IS NULL AND status='worker_error') OR (candidate_id IS NOT NULL AND source_sha256 IS NOT NULL AND objective_ack_id IS NOT NULL)"
        ),
        fk(
            ["owner_id", "evidence_object_id", "evidence_kind", "evidence_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "evidence_kind='search_worker_outcome' AND evidence_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchTerminalApplication(AttemptEvidence, Base):
    __tablename__ = "search_terminal_applications"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    frontier: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    previous_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    previous_frontier: Mapped[int | None] = mapped_column(Integer)
    worker_outcome_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    seed_candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    seed_source_sha256: Mapped[str | None] = mapped_column(String(64))
    seed_objective_ack_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    state_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    state_kind: Mapped[str] = mapped_column(String(40), default="search_population")
    state_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("epoch_id", "frontier"),
        UniqueConstraint("worker_outcome_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "frontier", "id"),
        UniqueConstraint(*SCOPE, "epoch_id", "frontier", "id", "state_sha256"),
        fk(
            [*SCOPE, "epoch_id", "previous_frontier", "previous_id"],
            "search_terminal_applications",
            [*SCOPE, "epoch_id", "frontier", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "frontier", "worker_outcome_id"],
            "search_worker_outcomes",
            [*SCOPE, "epoch_id", "native_iteration", "id"],
        ),
        fk(
            [
                *SCOPE,
                "epoch_id",
                "seed_candidate_id",
                "seed_source_sha256",
                "seed_objective_ack_id",
            ],
            "search_objective_acknowledgements",
            [*SCOPE, "epoch_id", "candidate_id", "source_sha256", "id"],
        ),
        fk(
            ["owner_id", "state_object_id", "state_kind", "state_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "state_kind='search_population' AND state_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "(frontier=0 AND outcome='seed' AND previous_id IS NULL AND previous_frontier IS NULL AND worker_outcome_id IS NULL AND seed_candidate_id IS NOT NULL AND seed_source_sha256 IS NOT NULL AND seed_objective_ack_id IS NOT NULL) OR (frontier>0 AND outcome IN ('accepted','rejected','error') AND previous_id IS NOT NULL AND previous_frontier=frontier-1 AND worker_outcome_id IS NOT NULL AND seed_candidate_id IS NULL AND seed_source_sha256 IS NULL AND seed_objective_ack_id IS NULL)"
        ),
    )


class SearchCheckpointIntent(AttemptEvidence, Base):
    __tablename__ = "search_checkpoint_intents"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    save_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frontier: Mapped[int] = mapped_column(Integer)
    terminal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    control_transition_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    state_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("epoch_id", "request_sha256"),
        UniqueConstraint("epoch_id", "save_ordinal"),
        CheckConstraint("save_ordinal IS NULL OR save_ordinal BETWEEN 1 AND 4"),
        UniqueConstraint(*SCOPE, "epoch_id", "id", "frontier", "state_sha256"),
        fk(
            [*SCOPE, "epoch_id", "frontier", "terminal_id"],
            "search_terminal_applications",
            [*SCOPE, "epoch_id", "frontier", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "frontier", "control_transition_id", "state_sha256"],
            "search_control_transitions",
            [*SCOPE, "epoch_id", "frontier", "id", "state_sha256"],
        ),
        CheckConstraint("frontier>=0 AND request_sha256 ~ '^[0-9a-f]{64}$'"),
    )


class SearchCheckpointPublication(AttemptEvidence, Base):
    """Stable native snapshot, explicitly not resumable whole-unit stop proof."""

    __tablename__ = "search_checkpoint_publications"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    frontier: Mapped[int] = mapped_column(Integer)
    state_sha256: Mapped[str] = mapped_column(String(64))
    inventory_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inventory_kind: Mapped[str] = mapped_column(
        String(40), default="search_checkpoint_inventory"
    )
    inventory_sha256: Mapped[str] = mapped_column(String(64))
    closure_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    closure_kind: Mapped[str] = mapped_column(
        String(40), default="search_checkpoint_closure"
    )
    closure_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "frontier", "state_sha256"],
            "search_checkpoint_intents",
            [*SCOPE, "epoch_id", "id", "frontier", "state_sha256"],
        ),
        fk(
            ["owner_id", "inventory_object_id", "inventory_kind", "inventory_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        fk(
            ["owner_id", "closure_object_id", "closure_kind", "closure_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "inventory_kind='search_checkpoint_inventory' AND closure_kind='search_checkpoint_closure' AND inventory_sha256 ~ '^[0-9a-f]{64}$' AND closure_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchEpochAbort(AttemptEvidence, Base):
    __tablename__ = "search_epoch_aborts"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(String(40))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("epoch_id"),
        fk([*SCOPE, "epoch_id"], "strategy_search_epochs", [*SCOPE, "id"]),
        CheckConstraint(
            "reason IN ('protocol_violation','uncertain_transport','state_untrusted','unit_failure','fenced')"
        ),
    )


class SearchCheckpointFile(AttemptEvidence, Base):
    __tablename__ = "search_checkpoint_files"
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(100))
    file_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(
        String(40), default="search_checkpoint_file"
    )
    object_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("publication_id", "name"),
        fk(
            [*SCOPE, "publication_id"], "search_checkpoint_publications", [*SCOPE, "id"]
        ),
        fk(
            ["owner_id", "object_id", "object_kind", "object_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "object_kind='search_checkpoint_file' AND file_sha256 ~ '^[0-9a-f]{64}$' AND object_sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 524288"
        ),
        CheckConstraint(
            "name ~ '^(metadata[.]json|best_program[.]py|best_program_info[.]json|programs/[0-9a-f-]{36}[.]json)$'"
        ),
    )


class SearchProposalResponse(AttemptEvidence, Base):
    __tablename__ = "search_proposal_responses"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rendered_context_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    model_outcome_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    response_kind: Mapped[str] = mapped_column(String(40), default="model_response")
    response_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("intent_id"),
        UniqueConstraint("model_outcome_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "intent_id", "id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "rendered_context_id"],
            "search_rendered_contexts",
            [*SCOPE, "epoch_id", "intent_id", "id"],
        ),
        fk([*SCOPE, "model_outcome_id"], "research_model_outcomes", [*SCOPE, "id"]),
        fk(
            ["owner_id", "response_object_id", "response_kind", "response_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "response_kind='model_response' AND response_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class SearchProposalSource(AttemptEvidence, Base):
    __tablename__ = "search_proposal_sources"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    proposal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage: Mapped[str] = mapped_column(String(16))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_bytes: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_kind: Mapped[str] = mapped_column(
        String(40), default="search_proposal_source"
    )
    object_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint(
            *SCOPE, "epoch_id", "intent_id", "source_sha256", "id", "stage", "evidence"
        ),
        UniqueConstraint("intent_id", "stage"),
        UniqueConstraint(*SCOPE, "epoch_id", "intent_id", "source_sha256", "id"),
        fk(
            [*SCOPE, "epoch_id", "intent_id", "proposal_id"],
            "search_proposal_responses",
            [*SCOPE, "epoch_id", "intent_id", "id"],
        ),
        fk(
            ["owner_id", "object_id", "object_kind", "object_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "stage IN ('applied','enforced') AND source_sha256 ~ '^[0-9a-f]{64}$' AND object_sha256 ~ '^[0-9a-f]{64}$' AND object_kind='search_proposal_source'"
        ),
        CheckConstraint(
            "(evidence='complete_source' AND source_bytes BETWEEN 0 AND 32768) OR (evidence='oversize_source_digest' AND source_bytes BETWEEN 32769 AND 16777216)"
        ),
    )


class SearchControlTransition(AttemptEvidence, Base):
    """Closed native state cursor, separate from the applied iteration frontier."""

    __tablename__ = "search_control_transitions"
    epoch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    previous_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    previous_sequence: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    frontier: Mapped[int] = mapped_column(Integer)
    terminal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sampling_intent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    state_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    state_kind: Mapped[str] = mapped_column(String(40), default="search_population")
    state_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scope_constraints() + (
        UniqueConstraint("epoch_id", "request_sha256"),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        UniqueConstraint("epoch_id", "sequence"),
        CheckConstraint("sequence BETWEEN 1 AND 5"),
        UniqueConstraint("terminal_id"),
        UniqueConstraint("sampling_intent_id"),
        UniqueConstraint(*SCOPE, "epoch_id", "sequence", "id"),
        UniqueConstraint(*SCOPE, "epoch_id", "frontier", "id", "state_sha256"),
        fk(
            [*SCOPE, "epoch_id", "previous_sequence", "previous_id"],
            "search_control_transitions",
            [*SCOPE, "epoch_id", "sequence", "id"],
        ),
        fk(
            [*SCOPE, "epoch_id", "frontier", "terminal_id", "state_sha256"],
            "search_terminal_applications",
            [*SCOPE, "epoch_id", "frontier", "id", "state_sha256"],
        ),
        fk(
            [*SCOPE, "epoch_id", "sampling_intent_id"],
            "search_iteration_intents",
            [*SCOPE, "epoch_id", "id"],
        ),
        fk(
            ["owner_id", "state_object_id", "state_kind", "state_sha256"],
            "research_objects",
            ["owner_id", "id", "object_kind", "sha256"],
        ),
        CheckConstraint(
            "state_kind='search_population' AND state_sha256 ~ '^[0-9a-f]{64}$' AND frontier BETWEEN 0 AND 2"
        ),
        CheckConstraint(
            "(sequence=1 AND kind='seed' AND frontier=0 AND previous_id IS NULL AND previous_sequence IS NULL AND terminal_id IS NOT NULL AND sampling_intent_id IS NULL) OR (sequence>1 AND previous_id IS NOT NULL AND previous_sequence=sequence-1 AND ((kind='sampling' AND terminal_id IS NULL AND sampling_intent_id IS NOT NULL) OR (kind='terminal' AND terminal_id IS NOT NULL AND sampling_intent_id IS NULL AND frontier>0)))"
        ),
    )

# Register dependent024 tables only after their shared scope/base definitions exist.
from backend.app import openevolve_event_models as _openevolve_event_models  # noqa: F401
from backend.app import openevolve_operational_models as _openevolve_operational_models  # noqa: F401
from backend.app import openevolve_worker_models as _openevolve_worker_models  # noqa: F401
