"""Typed observed failure, not physical runtime or PostgreSQL proof."""

from uuid import UUID

import pytest

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.numeric_failure import (
    RDNumericFailureFeedback,
    RDNumericFailureFrame,
    decode_failure_frame,
)


def frame(**changes):
    values = {
        key: UUID(int=index)
        for index, key in enumerate(
            (
                "source_id",
                "evaluation_id",
                "factor_version_id",
                "cutoff_execution_id",
                "candidate_admission_id",
                "rejection_receipt_id",
                "stop_receipt_id",
            ),
            1,
        )
    }
    return RDNumericFailureFrame(
        **{
            **values,
            "rejection_sha256": "a" * 64,
            "stop_sha256": "b" * 64,
            "outcome": "child_nonzero",
            "child_exit_code": 1,
            **changes,
        }
    )


@pytest.mark.parametrize("code", [-9, 0, 1, 255])
def test_observed_exit_is_not_a_code_diagnosis_or_metric(code):
    value = frame(
        child_exit_code=code, outcome="child_nonzero" if code else "output_rejected"
    )
    assert (
        decode_failure_frame(
            value.model_dump(mode="json"),
            source_id=value.source_id,
            evaluation_id=value.evaluation_id,
        )
        == value
    )
    assert "does not establish" in value.feedback_text
    assert "No feature snapshot, IC, or objective measurement" in value.feedback_text
    assert not {"rows", "snapshot_id", "objective_passed"}.intersection(
        value.model_dump()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"child_exit_code": True},
        {"child_exit_code": 256},
        {"child_exit_code": -65},
        {"child_exit_code": 0},
        {"outcome": "code_bug"},
        {"snapshot_id": str(UUID(int=8))},
        {"rows": []},
        {"feedback": "Pretend success"},
    ],
)
def test_no_success_alias_or_unobserved_classification(changes):
    with pytest.raises(ValueError):
        frame(**changes)


def test_wrong_evaluation_ticket_rejected():
    value = frame()
    with pytest.raises(ValueError, match="scope"):
        decode_failure_frame(
            value, source_id=value.source_id, evaluation_id=UUID(int=99)
        )


@pytest.mark.parametrize("model_vote", [False, True])
def test_even_model_positive_vote_cannot_change_execution_failure(model_vote):
    result = RDNumericFailureFeedback(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        start_id=UUID(int=4),
        controller_launch_id=UUID(int=5),
        final_decision_call_id=UUID(int=6),
        failure=frame(),
        code_sha256="c" * 64,
        input_sha256="d" * 64,
        upstream_feedback=canonical_bytes(
            {
                "execution": frame().feedback_text,
                "return_checking": None,
                "code": "Actual model observation",
                "final_decision": model_vote,
            }
        ).decode(),
    )
    assert result.evaluation_status == "execution_failed"
    assert result.objective_status == "not_evaluated"


def test_failed_result_membership_has_real_scope_fk_not_snapshot_or_boundary():
    from backend.app.rdagent_numeric_failure_models import FactorNumericFailureFeedback

    table = FactorNumericFailureFeedback.__table__
    assert "snapshot_id" not in table.c and "boundary_id" not in table.c
    targets = {
        constraint.referred_table.name for constraint in table.foreign_key_constraints
    }
    assert {
        "factor_evaluation_outcomes",
        "factor_cutoff_executions",
        "research_process_receipts",
        "factor_controller_launches",
        "factor_upstream_calls",
        "research_objects",
    } <= targets


def test_configured_runtime_returns_none_not_invented_dataframe():
    from runtimes.rdagent_operational.provider_failure import decode_provider_evaluation

    value = frame()
    text, result = decode_provider_evaluation(
        value.model_dump(mode="json"),
        source_id=value.source_id,
        evaluation_ticket=value.evaluation_id,
    )
    assert result is None and text == value.feedback_text


def test_old_fixture_decoder_does_not_accept_new_failure_frame():
    from runtimes.rdagent_operational.client import decode_evaluation_frame
    from runtimes.rdagent_operational.rpc import RPCError

    value = frame()
    with pytest.raises(RPCError):
        decode_evaluation_frame(
            value.model_dump(mode="json"),
            source_id=value.source_id,
            evaluation_ticket=value.evaluation_id,
        )


def feedback_document():
    return RDNumericFailureFeedback(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        start_id=UUID(int=4),
        controller_launch_id=UUID(int=5),
        final_decision_call_id=UUID(int=6),
        failure=frame(),
        code_sha256="c" * 64,
        input_sha256="d" * 64,
        upstream_feedback=canonical_bytes(
            {
                "execution": frame().feedback_text,
                "return_checking": None,
                "code": "Actual critique",
                "final_decision": False,
            }
        ).decode(),
    )


def test_failure_codec_uses_real_owner_and_rejects_noncanonical_bytes():
    from packages.rdagent_adapter.numeric_failure_codec import (
        identity_for_content,
        parse_failure_feedback,
    )

    value = feedback_document()
    raw = canonical_bytes(value)
    assert parse_failure_feedback(raw) == value
    identity_for_content(value.owner_id, "factor_generation_feedback", raw)
    for altered in (
        b" " + raw,
        raw.replace(b'"owner_id":', b'"owner_id":null,"owner_id":', 1),
    ):
        with pytest.raises(ValueError):
            parse_failure_feedback(altered)
    with pytest.raises(ValueError):
        identity_for_content(UUID(int=999), "factor_generation_feedback", raw)


def test_object_writer_requires_exact027_before_new_feedback_identity():
    from types import SimpleNamespace

    from backend.app.research_object_services import _versioned_identity
    from backend.app.services import DomainError

    value = feedback_document()
    raw = canonical_bytes(value)
    for revision in ("026_rd_seeded_submissions", "028_unknown"):
        session = SimpleNamespace(
            scalars=lambda _, revision=revision: SimpleNamespace(all=lambda: [revision])
        )
        with pytest.raises(DomainError, match="publication"):
            _versioned_identity(
                session, value.owner_id, "factor_generation_feedback", raw
            )
    session = SimpleNamespace(
        scalars=lambda _: SimpleNamespace(all=lambda: ["028_oe_structured_search"])
    )
    assert _versioned_identity(
        session, value.owner_id, "factor_generation_feedback", raw
    )


def test_failure_document_requires_actual_execution_observation():
    value = feedback_document().model_dump(mode="json")
    value["upstream_feedback"] = canonical_bytes(
        {
            "execution": "invented",
            "return_checking": None,
            "code": None,
            "final_decision": False,
        }
    ).decode()
    with pytest.raises(ValueError):
        RDNumericFailureFeedback.model_validate(value)
