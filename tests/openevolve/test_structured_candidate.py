"""027 pure closed-program proposals; not installed search/DB/engine acceptance."""

import hashlib
import json

import pytest

from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    TypedFrozenCandidateDomain,
    project_candidate,
)
from tests.structured.test_operator import declared_input


def setup_candidate():
    program = declared_input().program
    domain = TypedFrozenCandidateDomain(seed_program=program)
    value = CandidateDocument(program=program).model_dump(mode="json")
    return domain, value


@pytest.mark.parametrize(
    "field,value",
    [
        ("threshold", 0.02),
        ("top_k", 2),
        ("maximum_weight", 0.25),
        ("maximum_gross_weight", 0.75),
    ],
)
def test_only_declared_parameter_changes_project_exact_program(field, value):
    domain, document = setup_candidate()
    document["program"][field] = value
    raw = canonical_bytes(document)
    result = project_candidate(raw, domain)
    assert getattr(result.program, field) == value
    assert result.program_bytes == canonical_bytes(document["program"])
    assert result.program_sha256 == hashlib.sha256(result.program_bytes).hexdigest()
    assert result.candidate_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "signal_window",
        "eligibility_operator",
        "score_operator",
        "allocation_operator",
        "missing_value_policy",
        "empty_eligible_policy",
        "outside_signal_window",
        "tie_break",
    ],
)
def test_fixed_program_semantics_cannot_change(field):
    domain, document = setup_candidate()
    document["program"][field] = (
        {"start": "2026-01-02", "end": "2026-01-03"}
        if field == "signal_window"
        else "caller_selected"
    )
    with pytest.raises(ValueError):
        project_candidate(canonical_bytes(document), domain)


@pytest.mark.parametrize(
    "raw",
    [
        b'print("not a program")',
        b"```json\n{}\n```",
        b'{"x":1,"x":2}',
        b'{"program":{"top_k":1,"top_k":2}}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"\xff",
        b"",
        b"x" * 32769,
    ],
)
def test_ambiguous_nonfinite_python_and_unbounded_wire_rejected(raw):
    domain, _ = setup_candidate()
    with pytest.raises(ValueError):
        project_candidate(raw, domain)


@pytest.mark.parametrize(
    "change",
    ["whitespace", "extra", "boolean", "label", "domain_bypass", "program_bypass"],
)
def test_no_normalization_or_unchecked_model_copy_authority(change):
    domain, document = setup_candidate()
    if change == "whitespace":
        raw = json.dumps(document, indent=2).encode()
    else:
        if change == "extra":
            document["source"] = "import os"
        elif change == "boolean":
            document["program"]["top_k"] = True
        elif change == "label":
            document["program"]["labels"] = [1.0]
        elif change == "domain_bypass":
            domain = domain.model_copy(update={"allowed_mutations": ("signal_window",)})
        elif change == "program_bypass":
            domain = domain.model_copy(
                update={
                    "seed_program": domain.seed_program.model_copy(
                        update={"top_k": True}
                    )
                }
            )
        raw = canonical_bytes(document)
    with pytest.raises(ValueError):
        project_candidate(raw, domain)


def test_seed_identity_is_permitted_but_not_a_claim_of_evolution():
    domain, document = setup_candidate()
    result = project_candidate(canonical_bytes(document), domain)
    assert result.program == domain.seed_program
    assert not hasattr(result, "admission_id")
    assert not hasattr(result, "snapshot_id")
