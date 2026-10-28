"""Owner-scoped input origin, not model supervision or human approval authority."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, text

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.engine_run_submission_schemas import RDSubmissionRequest
from backend.app.engine_run_views import owned_engine_run, rd_config
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.factor_models import FactorVersion
from backend.app.rdagent_models import (
    FactorGenerationModelOperation,
    FactorGenerationSource,
    FactorResponseAssemblyPart,
)
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSeedRequest,
    RDProviderSubmissionRequest,
)
from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest
from backend.app.rdagent_source_witness_models import (
    FactorSourceReadPart,
    FactorSourceReadWitness,
)
from backend.app.rdagent_steer_services import verify_steer_snapshot
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_receipts import read_request, validate_saved_binding
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
)
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.rdagent_adapter.steer import SteerSnapshot

router = APIRouter()
Origin = Literal["user_provided", "agent_generated", "unknown"]


class InputOperation(BaseModel):
    operation_id: UUID
    request_sha256: str
    response_sha256: str


class ResearchInputItem(BaseModel):
    id: UUID
    origin: Origin
    default_pinned: bool
    created_at: datetime
    title: str
    provenance: Literal[
        "authenticated_submission_receipt",
        "model_response_source_witness",
        "origin_not_established",
    ]
    content_sha256: str | None = None
    submission: (
        RDSubmissionRequest
        | RDSeededSubmissionRequest
        | RDProviderSubmissionRequest
        | RDProviderSeedRequest
        | None
    ) = None
    steer_snapshot: SteerSnapshot | None = None
    steer_snapshot_sha256: str | None = None
    model_binding_sha256: str | None = None
    steer_evidence: (
        Literal["accepted_input_snapshot_not_execution_or_compliance"] | None
    ) = None
    factor_id: UUID | None = None
    factor_version_id: UUID | None = None
    operations: tuple[InputOperation, ...] = ()


class ResearchInputs(BaseModel):
    run_id: UUID
    group_order: tuple[Origin, ...] = ("user_provided", "agent_generated", "unknown")
    items: tuple[ResearchInputItem, ...]
    has_more_proposals: bool
    qualification: Literal["origin_not_ml_supervision_or_human_approval"] = (
        "origin_not_ml_supervision_or_human_approval"
    )


def mismatch():
    raise DomainError(
        "input_provenance_inconsistent", "Retained input provenance differs", 409
    )


def same_scope(row, source):
    return row is not None and all(
        getattr(row, key) == getattr(source, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    )


def frozen_input_metadata(session, run):
    config = rd_config(run)
    if config.schema_version == "mineagent.factor-research.v1":
        return {}
    if config.schema_version != "mineagent.factor-research.v2":
        mismatch()
    snapshot = verify_steer_snapshot(session, run.owner_id, config.steer_snapshot)
    if snapshot.sha256 != config.steer_snapshot_sha256:
        mismatch()
    return {
        "steer_snapshot": snapshot,
        "steer_snapshot_sha256": snapshot.sha256,
        "model_binding_sha256": config.model_binding.sha256,
        "steer_evidence": "accepted_input_snapshot_not_execution_or_compliance",
    }


def submission_item(session, run, row):
    # The normal023 production writer is called only by authenticated POST.
    # This identifies a submitted declaration, not who authored every field.
    validate_saved_binding(
        row,
        run,
        session.get(FactorAnalysisPlan, row.plan_id),
        run.owner_id,
        row.idempotency_key,
    )
    return ResearchInputItem(
        id=row.id,
        origin="user_provided",
        default_pinned=True,
        created_at=row.created_at,
        title="User-provided submission",
        provenance="authenticated_submission_receipt",
        content_sha256=row.request_sha256,
        submission=read_request(row),
        **frozen_input_metadata(session, run),
    )


def seeded_submission_item(session, run, row):
    from backend.app.rdagent_models import FactorResearchSpec
    from backend.app.rdagent_seed_services import read_saved_seed
    from backend.app.rdagent_seed_submission import validate_saved_seed_binding

    validate_saved_seed_binding(
        row,
        run,
        session.get(FactorResearchSpec, row.spec_id),
        session.get(FactorAnalysisPlan, row.analysis_plan_id),
        run.owner_id,
        row.idempotency_key,
    )
    request, _ = read_saved_seed(row)
    return ResearchInputItem(
        id=row.id,
        origin="user_provided",
        default_pinned=True,
        created_at=row.created_at,
        title="User-provided seeded submission",
        provenance="authenticated_submission_receipt",
        content_sha256=row.request_sha256,
        submission=request,
        **frozen_input_metadata(session, run),
    )


def source_item(session, run, source, *, witnesses_available):
    if source.owner_id != run.owner_id or source.run_id != run.id:
        mismatch()
    version = session.get(FactorVersion, source.factor_version_id)
    if (
        version is None
        or version.owner_id != run.owner_id
        or version.code_sha256 != source.source_sha256
    ):
        mismatch()
    item = {
        "id": source.id,
        "origin": "unknown",
        "default_pinned": False,
        "created_at": source.created_at,
        "title": "Retained factor source — origin not established",
        "provenance": "origin_not_established",
        "content_sha256": source.source_sha256,
        "factor_id": version.factor_id,
        "factor_version_id": version.id,
    }
    if not witnesses_available:
        return ResearchInputItem(**item)
    witness = session.scalar(
        select(FactorSourceReadWitness).filter_by(
            owner_id=run.owner_id, run_id=run.id, source_id=source.id
        )
    )
    if witness is None:
        return ResearchInputItem(**item)
    if not same_scope(witness, source) or (
        witness.assembly_id,
        witness.call_id,
        witness.source_sha256,
    ) != (source.assembly_id, source.call_id, source.source_sha256):
        mismatch()
    parts = session.scalars(
        select(FactorSourceReadPart)
        .filter_by(owner_id=run.owner_id, run_id=run.id, witness_id=witness.id)
        .order_by(FactorSourceReadPart.ordinal)
        .limit(7)
    ).all()
    if not 1 <= len(parts) <= 6 or len(parts) != witness.part_count:
        mismatch()
    operations = []
    for ordinal, part in enumerate(parts):
        assembly_part = session.get(FactorResponseAssemblyPart, part.assembly_part_id)
        member = session.get(FactorGenerationModelOperation, part.member_id)
        operation = (
            session.get(ResearchModelOperation, member.operation_id) if member else None
        )
        outcome = session.get(ResearchModelOutcome, part.outcome_id)
        transport = (
            session.get(ResearchModelTransport, outcome.transport_id)
            if outcome
            else None
        )
        if (
            not all(
                same_scope(row, source)
                for row in (part, assembly_part, member, operation, outcome, transport)
            )
            or (
                assembly_part.assembly_id,
                assembly_part.member_id,
                assembly_part.outcome_id,
                assembly_part.response_sha256,
            )
            != (
                source.assembly_id,
                part.member_id,
                part.outcome_id,
                part.response_sha256,
            )
            or assembly_part.call_id != source.call_id
            or assembly_part.ordinal != part.ordinal
            or (assembly_part.response_object_id, assembly_part.response_object_kind)
            != (part.response_object_id, part.response_object_kind)
            or part.response_object_kind != "model_response"
            or member.start_id != source.start_id
            or member.protocol != operation.protocol
            or operation.context_id != source.context_id
            or transport.state != "completed"
            or member.context_id != source.context_id
            or part.ordinal != ordinal
            or member.call_id != source.call_id
            or transport.operation_id != operation.id
            or outcome.outcome != "completed"
            or operation.protocol
            != {
                "mineagent.factor-research.v1": "mineagent.rdagent-chat.v1",
                "mineagent.factor-research.v2": "mineagent.rdagent-provider-chat.v1",
            }.get(rd_config(run).schema_version)
            or (
                outcome.response_object_id,
                outcome.response_sha256,
                outcome.response_object_kind,
            )
            != (part.response_object_id, part.response_sha256, "model_response")
        ):
            mismatch()
        operations.append(
            InputOperation(
                operation_id=operation.id,
                request_sha256=operation.request_sha256,
                response_sha256=part.response_sha256,
            )
        )
    item.update(
        origin="agent_generated",
        title="Agent-generated factor proposal",
        provenance="model_response_source_witness",
        operations=tuple(operations),
    )
    return ResearchInputItem(**item)


@router.get("/engine-runs/{run_id}/inputs", response_model=ResearchInputs)
def read_inputs(
    run_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=100000),
):
    run = owned_engine_run(session, run_id, user.id)
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise DomainError(
            "input_history_schema_unavailable", "Exact historical schema required", 409
        )
    try:
        capabilities = capabilities_for(revisions[0])
    except ValueError:
        raise DomainError(
            "input_history_schema_unavailable", "Unsupported historical schema", 409
        ) from None
    rows = []
    receipt = None
    if (
        capabilities.rd_submission_schema
        == "mineagent.rd-submission-restore-closure.v1"
    ):
        receipt = session.scalar(
            select(FactorResearchSubmission).filter_by(owner_id=user.id, run_id=run.id)
        )
    seeded = None
    if capabilities.rd_seed_schema == "mineagent.rd-seed-restore-closure.v1":
        from backend.app.rdagent_seed_models import FactorSeededSubmission

        seeded = session.scalar(
            select(FactorSeededSubmission).filter_by(owner_id=user.id, run_id=run.id)
        )
    if receipt is not None and seeded is not None:
        mismatch()
    if receipt is not None:
        rows.append(submission_item(session, run, receipt))
    elif seeded is not None:
        rows.append(seeded_submission_item(session, run, seeded))
    else:
        rows.append(
            ResearchInputItem(
                id=run.id,
                origin="unknown",
                default_pinned=False,
                created_at=run.created_at,
                title="Run task origin not established",
                provenance="origin_not_established",
                content_sha256=rd_config(run).sha256,
            )
        )
    sources = session.scalars(
        select(FactorGenerationSource)
        .filter_by(owner_id=user.id, run_id=run.id)
        .order_by(FactorGenerationSource.created_at, FactorGenerationSource.id)
        .offset(offset)
        .limit(26)
    ).all()
    witnesses_available = capabilities.rd_source_closure_schema in {
        "mineagent.rd-source-restore-closure.v3",
        "mineagent.rd-source-restore-closure.v4",
        "mineagent.rd-source-restore-closure.v5",
        "mineagent.rd-source-restore-closure.v6",
    }
    rows.extend(
        source_item(session, run, source, witnesses_available=witnesses_available)
        for source in sources[:25]
    )
    rank = {"user_provided": 0, "agent_generated": 1, "unknown": 2}
    rows.sort(key=lambda item: (rank[item.origin], item.created_at, str(item.id)))
    return ResearchInputs(
        run_id=run.id, items=tuple(rows), has_more_proposals=len(sources) > 25
    )
