"""Normal027 disclosure projections; no raw provider messages or live grants."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.openevolve_structured.candidate import Closed
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.structured_factor_strategy.views import StructuredExecutionView


class NormalExecutionRecord(Closed):
    candidate_id: UUID
    parent_candidate_id: UUID | None
    ordinal: int = Field(ge=0, le=2)
    origin: Literal["authenticated_user_seed", "workflow_seed", "model_proposal"]
    program_version_id: UUID
    execution_id: UUID | None
    snapshot_id: UUID
    source_run_id: UUID
    qualification: Literal["identity_metadata_not_result_release"] = (
        "identity_metadata_not_result_release"
    )


class NormalExecutionRelease(Closed):
    run_id: UUID
    execution_id: UUID
    exposure_ids: tuple[UUID, ...] = Field(min_length=2, max_length=2)
    qualification: Literal["exposure_record_not_execution_authority"] = (
        "exposure_record_not_execution_authority"
    )
    extent: Literal["full_retained_factor_and_market_datasets"] = (
        "full_retained_factor_and_market_datasets"
    )


class NormalExecutionView(Closed):
    execution: StructuredExecutionView
    candidate_id: UUID
    measurement: StructuredMeasurement
    qualification: Literal["fixed_snapshot_comparison_not_causal_gain"] = (
        "fixed_snapshot_comparison_not_causal_gain"
    )


class NormalPairComparison(Closed):
    before: NormalExecutionView
    after: NormalExecutionView
    score_delta_percentage_points: float | None
    qualification: Literal["fixed_snapshot_comparison_not_causal_gain"] = (
        "fixed_snapshot_comparison_not_causal_gain"
    )
