"""Bounded owner stage navigation, never a stage status or execution grant."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import Contract, Sha256
from packages.research_workflow.contracts import StageKind


class WorkflowStageLink(Contract):
    stage_id: UUID
    ordinal: int = Field(strict=True, ge=1, le=256)
    kind: StageKind
    input_revision_id: UUID
    definition_sha256: Sha256
    created_at: datetime


class WorkflowStageList(Contract):
    workflow_id: UUID
    items: tuple[WorkflowStageLink, ...] = Field(max_length=64)
    after_ordinal: int = Field(strict=True, ge=0, le=256)
    has_more: bool
    qualification: Literal["navigation_metadata_not_stage_completion_or_authority"] = (
        "navigation_metadata_not_stage_completion_or_authority"
    )


def read_stage_list(session, owner_id, workflow_id, *, after_ordinal=0, limit=32):
    require_schema(session)
    if (
        type(after_ordinal) is not int
        or not 0 <= after_ordinal <= 256
        or type(limit) is not int
        or not 1 <= limit <= 64
    ):
        raise DomainError("invalid_workflow_page", "Invalid stage page", 422)
    root = session.scalar(
        select(ResearchWorkflow).filter_by(owner_id=owner_id, id=workflow_id)
    )
    if root is None:
        raise DomainError("not_found", "Workflow not found", 404)
    if (root.owner_id, root.id) != (owner_id, workflow_id):
        raise DomainError("workflow_history_changed", "Workflow scope differs", 409)
    rows = list(
        session.scalars(
            select(ResearchWorkflowStage)
            .where(
                ResearchWorkflowStage.owner_id == owner_id,
                ResearchWorkflowStage.workflow_id == workflow_id,
                ResearchWorkflowStage.ordinal > after_ordinal,
            )
            .order_by(ResearchWorkflowStage.ordinal)
            .limit(limit + 1)
        )
    )
    previous = after_ordinal
    for row in rows:
        if (row.owner_id, row.workflow_id) != (
            owner_id,
            workflow_id,
        ) or not previous < row.ordinal <= 256:
            raise DomainError(
                "workflow_history_changed", "Stage scope/order differs", 409
            )
        previous = row.ordinal
    return WorkflowStageList(
        workflow_id=workflow_id,
        after_ordinal=after_ordinal,
        has_more=len(rows) > limit,
        items=tuple(
            WorkflowStageLink(
                stage_id=row.id,
                ordinal=row.ordinal,
                kind=row.kind,
                input_revision_id=row.input_revision_id,
                definition_sha256=row.definition_sha256,
                created_at=row.created_at,
            )
            for row in rows[:limit]
        ),
    )
