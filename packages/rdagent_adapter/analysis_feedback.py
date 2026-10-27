"""022 declared-analysis feedback data, never execution or adoption authority.

The separate v2 schema cannot reinterpret historical maintained-fixture feedback.
Only the host's fresh complete snapshot + stopped analysis reader may populate it.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Sha256
from packages.rdagent_adapter.execution_outcomes import (
    DeclaredWindow,
    ExecutionEvidence,
)


class RDAnalysisFeedback(ExecutionEvidence):
    schema_version: Literal["mineagent.rd-execution-feedback.v2"] = (
        "mineagent.rd-execution-feedback.v2"
    )
    upstream_feedback: str = Field(
        strict=True, min_length=1, max_length=32768, pattern=r"^[^\x00]+$"
    )
    row_count: int = Field(strict=True, ge=1, le=10000)
    missing_count: int = Field(strict=True, ge=0, le=10000)
    declared_window: DeclaredWindow
    objective_protocol: Literal["declared_direct_factor_analysis.v1"] = (
        "declared_direct_factor_analysis.v1"
    )
    analysis_id: UUID
    analysis_result_sha256: Sha256
    objective_measurement_sha256: Sha256
    objective_status: Literal["passed", "failed", "not_evaluated"]
    objective_reason: Literal[
        "threshold_satisfied",
        "threshold_not_satisfied",
        "no_defined_days",
        "insufficient_coverage",
    ]

    @model_validator(mode="after")
    def objective_meaning(self):
        if self.missing_count > self.row_count:
            raise ValueError("Missing count exceeds complete snapshot rows")
        if (self.objective_status == "passed") != (
            self.objective_reason == "threshold_satisfied"
        ) or (self.objective_status == "failed") != (
            self.objective_reason == "threshold_not_satisfied"
        ):
            raise ValueError("Objective state and reason differ")
        return self
