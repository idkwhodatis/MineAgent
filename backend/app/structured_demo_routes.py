"""Explicit authenticated synthetic dataset creation, not research admission."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.concurrency import run_in_threadpool

from backend.app.api.deps import CurrentUser, SessionDep
from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.schemas import DatasetPublic
from backend.app.services import DomainError
from backend.app.structured_demo_services import (
    PUBLICATION_PEAK_BYTES,
    PUBLICATION_PEAK_FILES,
    publish_demo_dataset,
)
from packages.factors.contracts import DateWindow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.contracts import FactorTaskSpec
from packages.structured_factor_strategy.demo import QUALIFICATION, rd_preset

router = APIRouter()


class StructuredDemoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_synthetic: Literal[True]

    @field_validator("acknowledge_synthetic", mode="before")
    @classmethod
    def explicit_ack(cls, value):
        if value is not True:
            raise ValueError("Explicit synthetic-data acknowledgement required")
        return value


class StructuredDemoPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: DateWindow
    lookback_sessions: Literal[2]
    lag_sessions: Literal[0]
    input_fields: tuple[Literal["close"], Literal["volume"]]
    max_iterations: Literal[1]
    acknowledgement_required: Literal[True]
    task: FactorTaskSpec
    objective: DirectObjectiveDeclaration
    qualification: Literal["synthetic_by_construction_not_live_or_pit_evidence"]


class StructuredDemoReceipt(BaseModel):
    dataset: DatasetPublic
    qualification: Literal["synthetic_by_construction_not_live_or_pit_evidence"]
    rd_preset: StructuredDemoPreset
    research_submitted: Literal[False] = False


@router.post(
    "/engine-runs/demo-dataset",
    operation_id="create_structured_demo_dataset",
    response_model=StructuredDemoReceipt,
)
async def create_structured_demo_dataset(
    body: StructuredDemoCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
):
    """Repeat safely returns the same owned, byte-verified demo dataset.

    This route uses the same bearer authentication as existing data mutations.
    It neither enables a provider nor invokes a model, factor run or evaluator.
    The preset must still pass normal user acknowledgement/preflight/submit.
    """
    from packages.strategy_sdk import JsonLimits, parse_json

    try:
        exact = StructuredDemoCreate.model_validate(
            parse_json(
                await request.body(),
                limits=JsonLimits(max_bytes=1024, max_nodes=8, max_items=4),
            )
        )
        if exact != body:
            raise ValueError("normalization")
    except (ValueError, TypeError):
        raise DomainError(
            "invalid_demo_request",
            "Explicit bounded synthetic acknowledgement required",
            422,
        ) from None
    owner_id = user.id
    settings = request.app.state.settings

    def persist():
        with (
            shared_lifecycle(session.get_bind(), settings.storage_root),
            publication(
                session,
                owner_id,
                settings,
                peak_bytes=PUBLICATION_PEAK_BYTES,
                peak_files=PUBLICATION_PEAK_FILES,
            ) as admission,
        ):
            row = publish_demo_dataset(session, owner_id, settings, admission=admission)
            # Freeze response while the row is retained; do not trigger an
            # expired ORM reload after publication releases its quota/FD guards.
            result = StructuredDemoReceipt(
                dataset=DatasetPublic.model_validate(row),
                qualification=QUALIFICATION,
                rd_preset=rd_preset(),
            )
        # publication owns the bounded commit and post-commit deadline check.
        # No ready acknowledgement is returned if either is uncertain.
        return result

    return await run_in_threadpool(persist)
