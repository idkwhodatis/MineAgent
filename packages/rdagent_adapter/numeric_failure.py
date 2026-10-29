"""027 observed numeric rejection, never a code-root-cause or metric verdict."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes


class RDNumericFailureFrame(Contract):
    schema_version: Literal["mineagent.rdagent-numeric-rejection-frame.v1"] = (
        "mineagent.rdagent-numeric-rejection-frame.v1"
    )
    source_id: UUID
    evaluation_id: UUID
    factor_version_id: UUID
    cutoff_execution_id: UUID
    candidate_admission_id: UUID
    rejection_receipt_id: UUID
    rejection_sha256: Sha256
    stop_receipt_id: UUID
    stop_sha256: Sha256
    outcome: Literal["child_nonzero", "output_rejected"]
    child_exit_code: int = Field(strict=True, ge=-64, le=255)

    @model_validator(mode="after")
    def observed_exit(self):
        if (self.outcome == "child_nonzero") != (self.child_exit_code != 0):
            raise ValueError("Observed rejection kind and child exit differ")
        return self

    @property
    def feedback_text(self):
        return (
            "Protected candidate execution produced no accepted factor output. "
            f"Observed outcome={self.outcome}; child exit code={self.child_exit_code}; "
            f"rejection receipt={self.rejection_receipt_id}, SHA256={self.rejection_sha256}. "
            "This observation does not establish whether code, dependencies, or the "
            "environment caused the failure. No feature snapshot, IC, or objective "
            "measurement exists for this execution."
        )


class RDNumericFailureFeedback(Contract):
    schema_version: Literal["mineagent.rd-numeric-failure-feedback.v1"] = (
        "mineagent.rd-numeric-failure-feedback.v1"
    )
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    start_id: UUID
    controller_launch_id: UUID
    final_decision_call_id: UUID
    failure: RDNumericFailureFrame
    code_sha256: Sha256
    input_sha256: Sha256
    evaluation_status: Literal["execution_failed"] = "execution_failed"
    objective_status: Literal["not_evaluated"] = "not_evaluated"
    upstream_feedback: str = Field(strict=True, min_length=1, max_length=32768)

    @model_validator(mode="after")
    def upstream_observation(self):
        from packages.strategy_sdk import JsonLimits, parse_json

        raw = self.upstream_feedback.encode("utf-8")
        value = parse_json(
            raw, limits=JsonLimits(max_bytes=32768, max_nodes=32, max_items=8)
        )
        if (
            type(value) is not dict
            or set(value) != {"execution", "return_checking", "code", "final_decision"}
            or value["execution"] != self.failure.feedback_text
            or value["return_checking"] is not None
            or type(value["final_decision"]) is not bool
            or any(
                type(value[k]) not in {str, type(None)}
                for k in ("execution", "return_checking", "code")
            )
            or canonical_bytes(value) != raw
        ):
            raise ValueError("Exact bounded upstream feedback required")
        # Even a mistaken positive model vote is retained as an observation;
        # it cannot turn a protected execution rejection into adoption/success.
        return self


def decode_failure_frame(value, *, source_id, evaluation_id):
    frame = RDNumericFailureFrame.model_validate(value)
    if frame.source_id != source_id or frame.evaluation_id != evaluation_id:
        raise ValueError("Numeric failure frame scope differs")
    return frame
