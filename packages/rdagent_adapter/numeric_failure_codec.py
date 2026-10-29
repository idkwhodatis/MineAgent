"""Explicit 027 failed execution feedback; no historical codec widening."""

import hashlib

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.execution_object_identity import object_identity
from packages.rdagent_adapter.numeric_failure import RDNumericFailureFeedback


def parse_failure_feedback(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("Bounded numeric failure feedback required")
    value = RDNumericFailureFeedback.model_validate_json(raw)
    if canonical_bytes(value) != raw:
        raise ValueError("Exact canonical numeric failure feedback required")
    return value


def identity_for_content(owner, kind, raw):
    if kind != "factor_generation_feedback":
        raise ValueError("Numeric failure feedback kind required")
    value = parse_failure_feedback(raw)
    if value.owner_id != owner:
        raise ValueError("Numeric failure feedback owner differs")
    return object_identity(owner, kind, hashlib.sha256(raw).hexdigest())
