"""Lossless raw commitments, explicitly separate from lossy JSONB rendering."""

import hashlib
import json
import math
from types import SimpleNamespace

import pytest

from backend.app.factor_analysis_canonical import read_objective
from packages.factors.contracts import canonical_bytes
from packages.qlib_adapter.objective import DirectObjectiveDeclaration


def objective(value):
    return DirectObjectiveDeclaration(
        window={"start": "2024-03-13", "end": "2024-03-14"},
        label_horizon_sessions=1,
        metric="ic",
        direction="at_least",
        threshold=value,
        minimum_defined_days=1,
        minimum_pairs=2,
    )


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, -1.0, 1e-7, 1e-20, 5e-324, -5e-324])
def test_raw_objective_preserves_float_representation(value):
    declaration = objective(value)
    raw = canonical_bytes(declaration)
    payload = json.loads(raw)
    if value == 0:
        payload["threshold"] = 0  # simulate JSONB's signed-zero erasure
    actual = read_objective(
        SimpleNamespace(
            objective_canonical=raw.decode(),
            objective=payload,
            objective_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert canonical_bytes(actual) == raw
    assert math.copysign(1, actual.threshold) == math.copysign(1, value)


@pytest.mark.parametrize(
    "token", ["0.00", "-0", "1e-007", "NaN", "Infinity", "-Infinity"]
)
def test_typed_host_boundary_rejects_noncanonical_or_nonfinite_raw(token):
    raw = canonical_bytes(objective(0.0)).replace(
        b'"threshold":0.0', b'"threshold":' + token.encode()
    )
    with pytest.raises(ValueError):
        read_objective(
            SimpleNamespace(
                objective_canonical=raw.decode(),
                objective=json.loads(raw),
                objective_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
