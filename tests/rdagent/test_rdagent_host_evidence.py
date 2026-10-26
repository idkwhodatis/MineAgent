import hashlib
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ParseFailureReceipt,
    ResponseAssemblyDocument,
)


def part(index=1):
    return ModelResponsePart(
        member_id=UUID(int=index),
        outcome_id=UUID(int=index + 100),
        response_object_id=UUID(int=index + 200),
        response_sha256="a" * 64,
    )


def document(source="x=1\n"):
    return ResponseAssemblyDocument(
        call_id=UUID(int=9),
        orchestrator_admission_id=UUID(int=10),
        parts=(part(),),
        source=source,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
    )


def test_source_assembly_retains_invalid_python_and_json_roundtrip():
    value = document("def invalid syntax\n")
    assert (
        ResponseAssemblyDocument.model_validate_json(value.model_dump_json()) == value
    )
    assert value.source == "def invalid syntax\n"


def test_utf8_byte_cap_not_only_character_cap():
    with pytest.raises(ValidationError):
        document("汉" * 50000)


def test_exact_source_digest_and_unique_parts():
    raw = document().model_dump(mode="json")
    raw["source_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        ResponseAssemblyDocument.model_validate(raw)
    with pytest.raises(ValidationError):
        ParseFailureReceipt(
            call_id=UUID(int=9),
            orchestrator_admission_id=UUID(int=10),
            reason="invalid_json",
            parts=(part(), part()),
        )


def test_bounded_continuation_parts_and_closed_parse_reasons():
    with pytest.raises(ValidationError):
        ParseFailureReceipt(
            call_id=UUID(int=9),
            orchestrator_admission_id=UUID(int=10),
            reason="provider_timeout",
            parts=(part(),),
        )
    with pytest.raises(ValidationError):
        ParseFailureReceipt(
            call_id=UUID(int=9),
            orchestrator_admission_id=UUID(int=10),
            reason="invalid_json",
            parts=tuple(part(i) for i in range(1, 8)),
        )


def test_observed_adoption_is_not_the_next_repair_workspace():
    from packages.rdagent_adapter.host_evidence import ObservedGenerationDecision

    value = ObservedGenerationDecision(
        logical_step=1,
        evaluated_source_id=UUID(int=3),
        previous_adopted_source_id=UUID(int=2),
        adopted_source_id=UUID(int=2),
        continuation_source_id=UUID(int=3),
        adopted_new=False,
    )
    assert value.adopted_source_id != value.continuation_source_id
    assert (
        ObservedGenerationDecision.model_validate_json(value.model_dump_json()) == value
    )
    bad = value.model_dump(mode="json")
    bad["adopted_source_id"] = str(UUID(int=3))
    with pytest.raises(ValidationError):
        ObservedGenerationDecision.model_validate(bad)


def test_new_adoption_requires_actual_evaluated_source_and_exact_boolean():
    from packages.rdagent_adapter.host_evidence import ObservedGenerationDecision

    with pytest.raises(ValidationError):
        ObservedGenerationDecision(
            logical_step=0,
            evaluated_source_id=None,
            previous_adopted_source_id=None,
            adopted_source_id=None,
            continuation_source_id=None,
            adopted_new=True,
        )
    with pytest.raises(ValidationError):
        ObservedGenerationDecision(
            logical_step=0,
            evaluated_source_id=UUID(int=1),
            previous_adopted_source_id=None,
            adopted_source_id=UUID(int=1),
            continuation_source_id=UUID(int=1),
            adopted_new=1,
        )
