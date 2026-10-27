"""Explicit022 feedback-v2 codec; historical021 parser remains unchanged."""

import hashlib
import json

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.analysis_feedback import RDAnalysisFeedback
from packages.rdagent_adapter.execution_object_identity import object_identity
from packages.rdagent_adapter.execution_outcomes import MAX_DOCUMENT_BYTES
from packages.rdagent_adapter.execution_outcomes import parse_document as parse_v1


def parse_document(kind, raw):
    if kind != "factor_generation_feedback":
        return parse_v1(kind, raw)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        raise ValueError("Bounded feedback bytes required")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate feedback field")
            result[key] = value
        return result

    body = json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Nonfinite feedback")
        ),
    )
    if body.get("schema_version") == "mineagent.rd-execution-feedback.v1":
        return parse_v1(kind, raw)
    model = RDAnalysisFeedback.model_validate_json(raw)
    if canonical_bytes(model) != raw:
        raise ValueError("Canonical analysis feedback required")
    return model


def identity_for_content(owner, kind, raw):
    document = parse_document(kind, raw)
    if document.owner_id != owner:
        raise ValueError("Analysis feedback owner mismatch")
    return object_identity(owner, kind, hashlib.sha256(raw).hexdigest())
