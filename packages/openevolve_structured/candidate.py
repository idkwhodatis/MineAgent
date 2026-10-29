"""Closed JSON full-rewrite candidates for a proposed027 upstream profile.

This is data validation, NOT a replacement for persisted owner/snapshot/market/
objective binding. It does not enable the024 Python protocol or issue025 runs.
Upstream's permissive language fallback is never sufficient to admit a candidate.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from packages.factors.contracts import canonical_bytes
from packages.structured_factor_strategy.contracts import StructuredProgram

MAX_CANDIDATE_BYTES = 32768
MUTATIONS = ("threshold", "top_k", "maximum_weight", "maximum_gross_weight")


def _raw_fields(value):
    # Pydantic JSON serialization can normalize unchecked model_copy values
    # (e.g. bool in an int field). Rebuild from raw typed fields before dumping.
    if isinstance(value, BaseModel):
        if set(value.__dict__) != set(type(value).model_fields):
            raise ValueError("Unexpected unchecked candidate model fields")
        return {
            key: _raw_fields(getattr(value, key)) for key in type(value).model_fields
        }
    if type(value) is tuple:
        return tuple(_raw_fields(item) for item in value)
    if type(value) is list:
        return [_raw_fields(item) for item in value]
    if type(value) is dict:
        return {key: _raw_fields(item) for key, item in value.items()}
    return value


class Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class TypedFrozenCandidateDomain(Closed):
    """Host-selected mutation domain data, never a launch or snapshot grant."""

    schema_version: Literal["mineagent.oe-structured-candidate-domain.v1"] = (
        "mineagent.oe-structured-candidate-domain.v1"
    )
    seed_program: StructuredProgram
    allowed_mutations: tuple[
        Literal["threshold", "top_k", "maximum_weight", "maximum_gross_weight"], ...
    ] = MUTATIONS

    @model_validator(mode="after")
    def fixed(self):
        if self.allowed_mutations != MUTATIONS:
            raise ValueError("Exact closed mutation fields required")
        # Existing frozen instances/model_copy are not a validation shortcut.
        StructuredProgram.model_validate(_raw_fields(self.seed_program))
        return self


class CandidateDocument(Closed):
    schema_version: Literal["mineagent.oe-structured-candidate.v1"] = (
        "mineagent.oe-structured-candidate.v1"
    )
    program: StructuredProgram


@dataclass(frozen=True)
class ProjectedCandidate:
    """Immutable parsed data only; the eventual DB issuer must resolve it afresh."""

    program: StructuredProgram
    program_bytes: bytes
    program_sha256: str
    candidate_sha256: str


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate candidate JSON key")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("Nonfinite candidate JSON number")


def project_candidate(raw, domain):
    """Accept one exact canonical UTF-8 document, not prose/fences/Python source.

    Original upstream response/proposal evidence must be retained separately by
    the future protocol adapter. No silent normalization changes its candidate.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CANDIDATE_BYTES:
        raise ValueError("Candidate byte bound")
    if type(domain) is not TypedFrozenCandidateDomain:
        raise ValueError("Exact typed candidate domain required")
    domain = TypedFrozenCandidateDomain.model_validate(_raw_fields(domain))
    value = json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
    )
    candidate = CandidateDocument.model_validate_json(canonical_bytes(value))
    if canonical_bytes(candidate) != raw:
        raise ValueError("Exact canonical candidate bytes required")
    program = StructuredProgram.model_validate_json(canonical_bytes(candidate.program))
    seed_fields = domain.seed_program.model_dump(mode="json")
    fields = program.model_dump(mode="json")
    if set(fields) != set(seed_fields) or any(
        fields[key] != seed_fields[key] for key in fields if key not in MUTATIONS
    ):
        raise ValueError("Candidate changed immutable program semantics")
    program_bytes = canonical_bytes(program)
    return ProjectedCandidate(
        program,
        program_bytes,
        hashlib.sha256(program_bytes).hexdigest(),
        hashlib.sha256(raw).hexdigest(),
    )
