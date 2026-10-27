"""022 exact raw JSON commitments where JSONB erases float representation.

SQL verifies original-byte hashes and parsed semantic equality; it does not claim
Python float repr equivalence. Every host writer/read/cold boundary additionally
requires this typed exact Python canonical roundtrip (including signed zero).
Historical integer-only SQL codecs are deliberately unchanged.
"""

import hashlib

from packages.factors.contracts import canonical_bytes
from packages.qlib_adapter.objective import DirectObjectiveDeclaration


def decode_committed(raw, payload, sha256, model, maximum):
    if type(raw) is not str or type(payload) is not dict:
        raise ValueError("Exact raw analysis JSON commitment required")
    encoded = raw.encode("ascii")
    if not 0 < len(encoded) <= maximum or hashlib.sha256(encoded).hexdigest() != sha256:
        raise ValueError("Raw analysis JSON hash/size differs")
    value = model.model_validate_json(encoded)
    if canonical_bytes(value) != encoded or value.model_dump(mode="json") != payload:
        raise ValueError("Raw analysis JSON is not exact typed canonical bytes")
    return value


def read_objective(plan):
    get = plan.__getitem__ if isinstance(plan, dict) else lambda key: getattr(plan, key)
    return decode_committed(
        get("objective_canonical"),
        get("objective"),
        get("objective_sha256"),
        DirectObjectiveDeclaration,
        65536,
    )
