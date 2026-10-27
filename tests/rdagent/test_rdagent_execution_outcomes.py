"""Pure evidence grammar only; no measured execution or adoption authority."""

import hashlib
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.execution_outcomes import (
    RDExecutionAdoption,
    RDExecutionFeedback,
    RDExecutionProposal,
)

MODELS = (RDExecutionProposal, RDExecutionFeedback, RDExecutionAdoption)


def document(model):
    value = {
        key: uuid4()
        for key in (
            "owner_id",
            "run_id",
            "attempt_id",
            "start_id",
            "source_id",
            "evaluation_id",
            "snapshot_id",
            "factor_version_id",
        )
    }
    value.update(
        generation=1,
        **{
            key: "a" * 64
            for key in (
                "code_sha256",
                "input_sha256",
                "output_sha256",
                "manifest_sha256",
            )
        },
    )
    if model is RDExecutionProposal:
        value.update(source_assembly_id=uuid4(), source_assembly_sha256="b" * 64)
    elif model is RDExecutionFeedback:
        value.update(
            upstream_feedback="Actual upstream feedback",
            row_count=3,
            missing_count=0,
            declared_window={
                "start": "2024-01-01",
                "end": "2024-01-04",
                "complete": True,
            },
            objective_passed=True,
        )
    else:
        value.update(
            **{
                key: uuid4()
                for key in (
                    "observation_id",
                    "boundary_id",
                    "controller_launch_id",
                    "proposal_object_id",
                    "feedback_object_id",
                )
            },
            proposal_sha256="b" * 64,
            feedback_sha256="c" * 64,
            adopted_source_id=value["source_id"],
        )
    return value


@pytest.mark.parametrize("model", MODELS)
def test_canonical_roundtrip_closed_immutable(model):
    value = model.model_validate(document(model))
    raw = canonical_bytes(value)
    assert len(raw) <= 65536
    assert model.model_validate_json(raw) == value
    assert value.sha256 == hashlib.sha256(raw).hexdigest()
    assert (
        raw
        == json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    )
    with pytest.raises(ValidationError):
        value.generation = 2


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    "change", ["extra", "bool_generation", "bad_hash", "bad_uuid", "wrong_schema"]
)
def test_common_negative(model, change):
    value = document(model)
    key, replacement = {
        "extra": ("verified_source", True),
        "bool_generation": ("generation", True),
        "bad_hash": ("code_sha256", "A" * 64),
        "bad_uuid": ("snapshot_id", "not-uuid"),
        "wrong_schema": ("schema_version", "mineagent.rd-execution-unknown.v1"),
    }[change]
    value[key] = replacement
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("row_count", True),
        ("row_count", 0),
        ("row_count", 1001),
        ("missing_count", False),
        ("missing_count", -1),
        ("missing_count", 4),
        ("objective_passed", 1),
        ("objective_passed", "true"),
        ("upstream_feedback", ""),
        ("upstream_feedback", "x" * 32769),
        ("upstream_feedback", "x\0y"),
        ("upstream_feedback", "😀" * 6000),  # Canonical escaping breaches byte cap.
        ("objective_protocol", "generic_verified"),
    ],
)
def test_feedback_negative(key, value):
    data = document(RDExecutionFeedback)
    data[key] = value
    with pytest.raises(ValidationError):
        RDExecutionFeedback.model_validate(data)


@pytest.mark.parametrize("complete", [False, 1, "true", None])
def test_window_exact_complete(complete):
    data = document(RDExecutionFeedback)
    data["declared_window"]["complete"] = complete
    with pytest.raises(ValidationError):
        RDExecutionFeedback.model_validate(data)


@pytest.mark.parametrize("step", [True, False, 0.0, "0", 1, -1])
def test_adoption_step_exact_integer(step):
    data = document(RDExecutionAdoption)
    data["logical_step"] = step
    with pytest.raises(ValidationError):
        RDExecutionAdoption.model_validate(data)


def test_adoption_cannot_select_other_source():
    data = document(RDExecutionAdoption)
    data["adopted_source_id"] = uuid4()
    with pytest.raises(ValidationError):
        RDExecutionAdoption.model_validate(data)


def test_failure_feedback_remains_representable_not_acceptance():
    data = document(RDExecutionFeedback)
    data.update(objective_passed=False, missing_count=3)
    value = RDExecutionFeedback.model_validate(data)
    assert value.objective_passed is False and value.missing_count == value.row_count


@pytest.mark.parametrize(
    "kind,model",
    [
        ("factor_generation_proposal", RDExecutionProposal),
        ("factor_generation_feedback", RDExecutionFeedback),
        ("factor_adoption_receipt", RDExecutionAdoption),
    ],
)
def test_typed_document_api(kind, model):
    from packages.rdagent_adapter.execution_outcomes import (
        canonical_document,
        parse_document,
    )

    value = model.model_validate(document(model))
    raw = canonical_document(value)
    assert parse_document(kind, raw) == value
    for invalid in [
        raw + b"\n",
        b" " + raw,
        raw.replace(b'"generation":1', b'"generation":1,"generation":1'),
        raw.replace(b'"generation":1', b'"generation":true'),
        b"[]",
        b'"text"',
        b"NaN",
        b"\xff",
        b"x" * 65537,
    ]:
        with pytest.raises(ValueError):
            parse_document(kind, invalid)


def test_duplicate_nested_and_kind_mismatch():
    from packages.rdagent_adapter.execution_outcomes import (
        canonical_document,
        parse_document,
    )

    raw = canonical_document(
        RDExecutionFeedback.model_validate(document(RDExecutionFeedback))
    )
    with pytest.raises(ValueError, match="Duplicate"):
        parse_document(
            "factor_generation_feedback",
            raw.replace(b'"complete":true', b'"complete":true,"complete":true'),
        )
    with pytest.raises(ValueError):
        parse_document("factor_generation_proposal", raw)
    with pytest.raises(ValueError):
        parse_document("unknown", raw)
    with pytest.raises(ValueError):
        parse_document("factor_generation_feedback", raw.decode())
    with pytest.raises(ValueError):
        canonical_document({})


def test_canonical_document_revalidates_unsafe_constructed_model():
    from packages.rdagent_adapter.execution_outcomes import canonical_document

    value = RDExecutionFeedback.model_validate(document(RDExecutionFeedback))
    with pytest.raises(ValueError):
        canonical_document(value.model_copy(update={"row_count": True}))
