from uuid import UUID

import pytest

from packages.factors.contracts import digest
from packages.qlib_adapter.objective import DirectObjectiveMeasurement
from packages.research_inputs.contracts import SourceOutputRef
from packages.research_inputs.feedback import (
    FeedbackProjectionDefinition,
    project_direct_feedback,
)


def source():
    return SourceOutputRef(
        workflow_id=UUID(int=1),
        stage_id=UUID(int=2),
        child_run_id=UUID(int=3),
        output_kind="direct_analysis",
        output_id=UUID(int=4),
        content_sha256="a" * 64,
    )


def measurement(**changes):
    values = {
        "declaration_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "result_sha256": "a" * 64,
        "status": "failed",
        "reason": "threshold_not_satisfied",
        "value": -0.25,
        "defined_days": 1,
        "total_pairs": 3,
        "defined_pairs": 2,
    }
    values.update(changes)
    return DirectObjectiveMeasurement(**values)


def test_actual_measurement_projection_has_exact_refs_not_authority():
    value = measurement()
    result = project_direct_feedback(
        FeedbackProjectionDefinition(source=source()), value, (UUID(int=5), UUID(int=6))
    )
    assert result.source == source() and result.measurement_sha256 == digest(value)
    assert "failed" in result.text and "-0.25" in result.text and "2/3" in result.text
    assert "not out-of-sample performance" in result.text
    assert "approval_authority" not in type(result).model_fields


def test_undefined_is_not_reported_as_zero():
    result = project_direct_feedback(
        FeedbackProjectionDefinition(source=source()),
        measurement(
            status="not_evaluated",
            reason="no_defined_days",
            value=None,
            defined_days=0,
            defined_pairs=0,
        ),
        (UUID(int=5), UUID(int=6)),
    )
    assert "value: undefined" in result.text


@pytest.mark.parametrize(
    "case", ["result_hash", "coverage", "duplicate_exposure", "structured_source"]
)
def test_substituted_or_unsupported_sources_rejected(case):
    ref, value, ids = source(), measurement(), (UUID(int=5), UUID(int=6))
    if case == "result_hash":
        value = measurement(result_sha256="d" * 64)
    elif case == "coverage":
        value = measurement(defined_pairs=4)
    elif case == "duplicate_exposure":
        ids = (UUID(int=5), UUID(int=5))
    else:
        ref = ref.model_copy(update={"output_kind": "structured_execution"})
    with pytest.raises(ValueError):
        project_direct_feedback(FeedbackProjectionDefinition(source=ref), value, ids)


def test_no_caller_release_or_approval_boolean():
    with pytest.raises(ValueError):
        FeedbackProjectionDefinition(source=source(), approved=True)
