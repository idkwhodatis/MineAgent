"""Deterministic development feedback formatting, never release authorization.

Only an owning host may call this after actual stage/source verification and the
existing exposure ledger allows reading these values. Parsing these DTOs or
supplying UUIDs proves none of those conditions. No sealed/independent kind exists.
"""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, digest
from packages.qlib_adapter.objective import DirectObjectiveMeasurement
from packages.research_inputs.contracts import SourceOutputRef


class FeedbackProjectionDefinition(Contract):
    schema_version: Literal["mineagent.feedback-projection-stage.v1"] = (
        "mineagent.feedback-projection-stage.v1"
    )
    projection_version: Literal["mineagent.direct-development-feedback.v1"] = (
        "mineagent.direct-development-feedback.v1"
    )
    source: SourceOutputRef

    @model_validator(mode="after")
    def supported_source(self):
        if self.source.output_kind != "direct_analysis":
            raise ValueError("Only direct-analysis development projection is defined")
        return self


class ExperimentFeedbackProposal(Contract):
    schema_version: Literal["mineagent.experiment-feedback-proposal.v1"] = (
        "mineagent.experiment-feedback-proposal.v1"
    )
    origin: Literal["experiment_feedback_proposal"] = "experiment_feedback_proposal"
    projection_version: Literal["mineagent.direct-development-feedback.v1"] = (
        "mineagent.direct-development-feedback.v1"
    )
    source: SourceOutputRef
    measurement_sha256: Sha256
    exposure_ids: tuple[UUID, UUID]
    kind: Literal["direction"] = "direction"
    title: Literal["Previous development objective and coverage"] = (
        "Previous development objective and coverage"
    )
    text: str = Field(min_length=1, max_length=2048)
    qualification: Literal[
        "owned_development_measurement_not_out_of_sample_or_adoption_evidence"
    ] = "owned_development_measurement_not_out_of_sample_or_adoption_evidence"

    @model_validator(mode="after")
    def exact_shape(self):
        if (
            self.source.output_kind != "direct_analysis"
            or len(set(self.exposure_ids)) != 2
        ):
            raise ValueError(
                "Exact supported source and distinct exposure references required"
            )
        if len(self.text.encode("utf-8")) > 8192 or any(
            ord(c) < 32 or ord(c) == 127 for c in self.text
        ):
            raise ValueError("Bounded deterministic feedback text required")
        return self

    @property
    def sha256(self):
        return digest(self)


def project_direct_feedback(definition, measurement, exposure_ids):
    """Data-only projection; live/cold owning reader must independently rederive."""
    definition = FeedbackProjectionDefinition.model_validate(definition)
    measurement = DirectObjectiveMeasurement.model_validate(measurement)
    if measurement.result_sha256 != definition.source.content_sha256:
        raise ValueError("Measurement does not bind the referenced result")
    if measurement.defined_pairs > measurement.total_pairs:
        raise ValueError("Defined pair coverage exceeds total pairs")
    value = (
        "undefined"
        if measurement.value is None
        else json.dumps(measurement.value, allow_nan=False, separators=(",", ":"))
    )
    text = (
        f"The previous development factor objective was {measurement.status} "
        f"({measurement.reason}); observed objective value: {value}. "
        f"Defined days: {measurement.defined_days}; defined pairs: "
        f"{measurement.defined_pairs}/{measurement.total_pairs}. "
        "For the next research stage, inspect the prior factor and propose a distinct "
        "bounded hypothesis while preserving the declared dataset, window and objective. "
        "These are development observations, not out-of-sample performance, adoption "
        "approval or evidence that a proposed change will improve performance."
    )
    return ExperimentFeedbackProposal(
        source=definition.source,
        measurement_sha256=digest(measurement),
        exposure_ids=tuple(exposure_ids),
        text=text,
    )
