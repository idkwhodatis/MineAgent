"""Versioned feedback grammar only; no passed objective or adoption proof."""

from uuid import uuid4

import pytest

from packages.rdagent_adapter.analysis_feedback import RDAnalysisFeedback
from packages.rdagent_adapter.execution_outcomes import RDExecutionFeedback


def payload():
    return dict(
        **{
            k: uuid4()
            for k in (
                "owner_id",
                "run_id",
                "attempt_id",
                "start_id",
                "source_id",
                "evaluation_id",
                "snapshot_id",
                "factor_version_id",
                "analysis_id",
            )
        },
        **{
            k: "a" * 64
            for k in (
                "code_sha256",
                "input_sha256",
                "output_sha256",
                "manifest_sha256",
                "analysis_result_sha256",
                "objective_measurement_sha256",
            )
        },
        generation=1,
        upstream_feedback="Measured result available",
        row_count=3,
        missing_count=1,
        declared_window={"start": "2024-03-12", "end": "2024-03-13", "complete": True},
        objective_status="not_evaluated",
        objective_reason="no_defined_days",
    )


def test_undefined_is_not_boolean_success_and_old_codec_rejects_it():
    value = RDAnalysisFeedback(**payload())
    assert value.objective_status == "not_evaluated"
    assert "objective_passed" not in value.model_dump()
    with pytest.raises(ValueError):
        RDExecutionFeedback.model_validate(value.model_dump())


@pytest.mark.parametrize(
    "status,reason",
    [
        ("passed", "no_defined_days"),
        ("failed", "threshold_satisfied"),
        ("not_evaluated", "threshold_not_satisfied"),
    ],
)
def test_false_objective_status_rejected(status, reason):
    raw = payload()
    raw.update(objective_status=status, objective_reason=reason)
    with pytest.raises(ValueError):
        RDAnalysisFeedback(**raw)


def test_fixture_boolean_cannot_be_coerced_into_v2():
    raw = payload()
    raw["objective_passed"] = True
    with pytest.raises(ValueError):
        RDAnalysisFeedback(**raw)
