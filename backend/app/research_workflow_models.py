"""Unregistered successor tables for durable between-stage control.

Only a future explicit migration/capability may activate these services. Engine
stages bind existing owned runs; local normalization stages never fabricate one.
Selected input references, actual prompt evidence and completed outputs occupy
separate records. No record is a trust marker or a physical process grant.
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


def owner_fk():
    return ForeignKeyConstraint(["owner_id"], ["users.id"])


def workflow_fk():
    return ForeignKeyConstraint(
        ["owner_id", "workflow_id"],
        ["research_workflows.owner_id", "research_workflows.id"],
    )


class ResearchWorkflow(Base):
    __tablename__ = "research_workflows"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer)
    creation_key: Mapped[str] = mapped_column(String(128))
    creation_canonical: Mapped[str] = mapped_column(Text)
    creation_sha256: Mapped[str] = mapped_column(String(64))
    creation_result_canonical: Mapped[str] = mapped_column(Text)
    # Additional finite workflow admission ceiling; not a second spending ledger.
    # Every child reserves its full declared limits against this immutable cap.
    plan_canonical: Mapped[str] = mapped_column(Text)
    plan_sha256: Mapped[str] = mapped_column(String(64))
    budget_canonical: Mapped[str] = mapped_column(Text)
    budget_sha256: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    blocked_reason: Mapped[str | None] = mapped_column(String(80))
    current_input_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    current_stage_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    latest_checkpoint_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        owner_fk(),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "creation_key"),
        CheckConstraint("creation_key ~ '^[A-Za-z0-9_.:-]{1,128}$'"),
        CheckConstraint("octet_length(creation_result_canonical) BETWEEN 1 AND 4096"),
        CheckConstraint("octet_length(creation_canonical) BETWEEN 1 AND 1100000"),
        CheckConstraint("creation_sha256 ~ '^[a-f0-9]{64}$'"),
        CheckConstraint("revision BETWEEN 1 AND 1000000"),
        CheckConstraint(
            "plan_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(plan_canonical) BETWEEN 1 AND 1048576"
        ),
        CheckConstraint(
            "budget_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(budget_canonical) BETWEEN 1 AND 4096"
        ),
        CheckConstraint("mode IN ('continuous','pause_after_stage')"),
        CheckConstraint(
            "status IN ('ready','running','pause_requested','paused','cancel_requested','cancelled','blocked','completed')"
        ),
        ForeignKeyConstraint(
            ["owner_id", "id", "current_input_revision_id"],
            [
                "research_workflow_revisions.owner_id",
                "research_workflow_revisions.workflow_id",
                "research_workflow_revisions.id",
            ],
            use_alter=True,
            name="fk_workflow_input_head",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "id", "current_stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
            use_alter=True,
            name="fk_workflow_stage_head",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["owner_id", "id", "latest_checkpoint_id"],
            [
                "research_workflow_checkpoints.owner_id",
                "research_workflow_checkpoints.workflow_id",
                "research_workflow_checkpoints.id",
            ],
            use_alter=True,
            name="fk_workflow_checkpoint_head",
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class ResearchWorkflowRevision(Base):
    __tablename__ = "research_workflow_revisions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    inputs_canonical: Mapped[str] = mapped_column(Text)
    inputs_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        workflow_fk(),
        UniqueConstraint("owner_id", "workflow_id", "id"),
        UniqueConstraint("workflow_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 1000000"),
        CheckConstraint(
            "inputs_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(inputs_canonical) BETWEEN 1 AND 32768"
        ),
    )


class ResearchWorkflowStage(Base):
    __tablename__ = "research_workflow_stages"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    input_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(40))
    child_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    # Immutable definition commitment; execution state lives in the real child
    # run or normalization receipt, never duplicated as a caller-written flag.
    definition_canonical: Mapped[str] = mapped_column(Text)
    definition_sha256: Mapped[str] = mapped_column(String(64))
    allocation_canonical: Mapped[str] = mapped_column(Text)
    allocation_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        workflow_fk(),
        UniqueConstraint("owner_id", "workflow_id", "id"),
        UniqueConstraint("workflow_id", "ordinal"),
        UniqueConstraint("child_run_id"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "input_revision_id"],
            [
                "research_workflow_revisions.owner_id",
                "research_workflow_revisions.workflow_id",
                "research_workflow_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "child_run_id"],
            ["research_runs.owner_id", "research_runs.id"],
            name="fk_workflow_stage_child",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 256"),
        CheckConstraint(
            "allocation_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(allocation_canonical) BETWEEN 1 AND 4096"
        ),
        CheckConstraint(
            "(kind IN ('external_normalization','experiment_feedback_projection') AND child_run_id IS NULL) OR (kind IN ('rd_factor_generation','structured_factor_execution') AND child_run_id IS NOT NULL)"
        ),
        CheckConstraint(
            "definition_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(definition_canonical) BETWEEN 1 AND 1048576"
        ),
    )


class ResearchWorkflowCheckpoint(Base):
    __tablename__ = "research_workflow_checkpoints"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    # Logical retained terminal graph commitments, not a serialized resume token.
    terminal_canonical: Mapped[str] = mapped_column(Text)
    terminal_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        workflow_fk(),
        UniqueConstraint("owner_id", "workflow_id", "id"),
        UniqueConstraint("stage_id"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
        ),
        CheckConstraint(
            "terminal_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(terminal_canonical) BETWEEN 1 AND 1048576"
        ),
    )


class ResearchWorkflowCommand(Base):
    __tablename__ = "research_workflow_commands"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    command_canonical: Mapped[str] = mapped_column(Text)
    command_sha256: Mapped[str] = mapped_column(String(64))
    result_canonical: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        workflow_fk(),
        UniqueConstraint("owner_id", "workflow_id", "idempotency_key"),
        CheckConstraint(
            "command_sha256 ~ '^[a-f0-9]{64}$' AND octet_length(command_canonical) BETWEEN 1 AND 32768 AND octet_length(result_canonical) BETWEEN 1 AND 4096"
        ),
    )


class ResearchWorkflowModelConsumption(Base):
    """Actual submitted operation evidence, not compliance or output success."""

    __tablename__ = "research_workflow_model_consumption"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    stage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    input_sha256: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    generation: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    transport_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    request_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        workflow_fk(),
        UniqueConstraint("stage_id", "operation_id"),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "stage_id"],
            [
                "research_workflow_stages.owner_id",
                "research_workflow_stages.workflow_id",
                "research_workflow_stages.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "workflow_id", "input_revision_id"],
            [
                "research_workflow_revisions.owner_id",
                "research_workflow_revisions.workflow_id",
                "research_workflow_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "operation_id"],
            [
                "research_model_operations.owner_id",
                "research_model_operations.run_id",
                "research_model_operations.attempt_id",
                "research_model_operations.generation",
                "research_model_operations.id",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id", "attempt_id", "generation", "transport_id"],
            [
                "research_model_transports.owner_id",
                "research_model_transports.run_id",
                "research_model_transports.attempt_id",
                "research_model_transports.generation",
                "research_model_transports.id",
            ],
        ),
        CheckConstraint(
            "generation > 0 AND input_sha256 ~ '^[a-f0-9]{64}$' AND request_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )
