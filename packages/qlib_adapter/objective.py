"""Predeclared direct-factor objectives; not provider quality or execution proof."""

from typing import Literal

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, DateWindow, Sha256, digest
from packages.qlib_adapter.contracts import DirectResult
from packages.qlib_adapter.execution_binding import DirectAnalysisProjection


class DirectObjectiveDeclaration(Contract):
    schema_version: Literal["mineagent.direct-factor-objective.v1"] = (
        "mineagent.direct-factor-objective.v1"
    )
    kind: Literal["direct_factor_analysis"] = "direct_factor_analysis"
    window: DateWindow
    label_horizon_sessions: int = Field(strict=True, ge=1, le=4096)
    metric: Literal["ic", "rank_ic"]
    direction: Literal["at_least", "at_most"]
    threshold: float = Field(strict=True, ge=-1, le=1)
    minimum_defined_days: int = Field(strict=True, ge=1, le=4096)
    minimum_pairs: int = Field(strict=True, ge=2, le=10_000)
    aggregation: Literal["arithmetic_mean_of_defined_days"] = (
        "arithmetic_mean_of_defined_days"
    )
    selection_claim: Literal["research_selection_not_out_of_sample_performance"] = (
        "research_selection_not_out_of_sample_performance"
    )

    @property
    def sha256(self):
        return digest(self)


class DirectObjectiveMeasurement(Contract):
    schema_version: Literal["mineagent.direct-factor-objective-measurement.v1"] = (
        "mineagent.direct-factor-objective-measurement.v1"
    )
    declaration_sha256: Sha256
    protocol_sha256: Sha256
    result_sha256: Sha256
    status: Literal["passed", "failed", "not_evaluated"]
    reason: Literal[
        "threshold_satisfied",
        "threshold_not_satisfied",
        "no_defined_days",
        "insufficient_coverage",
    ]
    value: float | None = Field(default=None, ge=-1, le=1)
    defined_days: int = Field(strict=True, ge=0, le=4096)
    total_pairs: int = Field(strict=True, ge=0, le=10_000)
    defined_pairs: int = Field(strict=True, ge=0, le=10_000)
    qualification: Literal["measurement_not_execution_or_adoption_authority"] = (
        "measurement_not_execution_or_adoption_authority"
    )

    @model_validator(mode="after")
    def state(self):
        if (
            (self.status == "passed") != (self.reason == "threshold_satisfied")
            or (self.status == "failed") != (self.reason == "threshold_not_satisfied")
            or (self.status == "not_evaluated" and self.value is not None)
            or (self.status != "not_evaluated" and self.value is None)
        ):
            raise ValueError("Objective status/value/reason disagree")
        return self


def measure_direct_objective(declaration, projection, result):
    declaration = DirectObjectiveDeclaration.model_validate(declaration)
    if type(projection) is not DirectAnalysisProjection:
        raise ValueError("Explicit complete direct analysis projection required")
    result = DirectResult.model_validate(result)
    protocol = projection.protocol
    days = tuple(sorted({row.session for row in projection.factors}))
    if (
        digest([r.model_dump(mode="json") for r in projection.factors])
        != protocol.factor_points_sha256
        or digest([r.model_dump(mode="json") for r in projection.labels])
        != protocol.label_manifest.rows_sha256
        or tuple((r.session, r.listing_id) for r in projection.factors)
        != tuple((r.session, r.listing_id) for r in projection.labels)
        or declaration.window != protocol.window
        or declaration.label_horizon_sessions
        != protocol.label_manifest.horizon_sessions
        or result.protocol_sha256 != protocol.sha256
        or tuple(day.session for day in result.days) != days
        or declaration.minimum_defined_days > len(days)
    ):
        raise ValueError("Objective or measured series substitutes declared analysis")
    # Recheck pair coverage from immutable primitive inputs, not result claims.
    paired = {day: [] for day in days}
    for factor, label in zip(projection.factors, projection.labels, strict=True):
        if factor.value is not None and label.value is not None:
            paired[factor.session].append((factor.value, label.value))
    for day in result.days:
        values = paired[day.session]
        reason = (
            "insufficient_pairs"
            if len(values) < 2
            else "constant_cross_section"
            if len({p[0] for p in values}) < 2 or len({p[1] for p in values}) < 2
            else "defined"
        )
        if day.pair_count != len(values) or day.reason != reason:
            raise ValueError(
                "Measured coverage/undefined reason differs from primitive inputs"
            )
    defined_pairs = sum(
        day.pair_count for day in result.days if day.reason == "defined"
    )
    value = getattr(result, declaration.metric)
    if result.defined_days == 0:
        status, reason, value = "not_evaluated", "no_defined_days", None
    elif (
        result.defined_days < declaration.minimum_defined_days
        or defined_pairs < declaration.minimum_pairs
    ):
        status, reason, value = "not_evaluated", "insufficient_coverage", None
    else:
        passed = (
            value >= declaration.threshold
            if declaration.direction == "at_least"
            else value <= declaration.threshold
        )
        status, reason = (
            ("passed", "threshold_satisfied")
            if passed
            else ("failed", "threshold_not_satisfied")
        )
    return DirectObjectiveMeasurement(
        declaration_sha256=declaration.sha256,
        protocol_sha256=protocol.sha256,
        result_sha256=digest(result),
        status=status,
        reason=reason,
        value=value,
        defined_days=result.defined_days,
        total_pairs=result.total_pairs,
        defined_pairs=defined_pairs,
    )
