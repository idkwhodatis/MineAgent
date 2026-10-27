"""Actual pinned AKQuant observation proof, not Docker/admission/search acceptance."""

from datetime import date
from uuid import uuid4

import pytest

from packages.openevolve_adapter.contracts import DevelopmentWindow, ObjectiveProfile
from packages.openevolve_adapter.objective import (
    native_observation,
    population_metrics,
    score,
)
from packages.tradegrad_adapter.results import canonical_json_bytes, sha256_bytes
from tests.research_trace.test_independent_native_results import native


@pytest.mark.parametrize("scenario", ["normal", "no_trade", "rejected"])
def test_actual_native_observation_objective_uses_unrounded_basis(scenario):
    bundle = native(scenario)["bundle"]
    raw = {item["name"]: item["value"] for item in bundle["native_metrics"]}
    observation = native_observation(
        bundle,
        window_id="dev",
        evaluation_id=uuid4(),
        expected_bundle_sha256=sha256_bytes(canonical_json_bytes(bundle)),
    )
    profile = ObjectiveProfile(
        windows=(
            DevelopmentWindow(
                id="dev",
                start=date(2024, 1, 2),
                end=date(2024, 1, 12),
                weight_millionths=1_000_000,
            ),
        )
    )
    result = score(profile, (observation,))
    if raw["annualized_return"] is None or raw["max_drawdown_pct"] is None:
        assert result.eligible is False and result.combined_score is None
        with pytest.raises(ValueError):
            population_metrics(result)
    else:
        assert result.eligible
        assert (
            result.combined_score
            == raw["annualized_return"] * 100 - raw["max_drawdown_pct"]
        )
        assert population_metrics(result) == {"combined_score": result.combined_score}
    assert bundle["result_trust"] == "native_strategy_and_engine_share_process"


@pytest.mark.parametrize("seed", [0, 42, 2**32 - 1])
def test_seeded_state_matches_pinned_numpy_without_changing_host_rng(seed):
    import json
    import os
    import random
    import subprocess

    from packages.openevolve_adapter.execution_inputs import seeded_rng_state

    executable = os.environ.get("TRADEGRAD_NATIVE_TEST_PYTHON")
    if not executable:
        if os.environ.get("TRADEGRAD_NATIVE_TEST_REQUIRED") == "1":
            pytest.fail("Required native Python is not configured")
        pytest.skip("Native Python not configured")
    before = random.getstate()
    actual = seeded_rng_state(seed)
    assert random.getstate() == before
    script = (
        "import json,numpy,random;"
        f"random.seed({seed});numpy.random.seed({seed});"
        "from packages.tradegrad_adapter.runtime_observation import capture_rng_state;"
        "print(json.dumps(capture_rng_state(include_numpy=True)))"
    )
    result = subprocess.run(
        [executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert actual == json.loads(result.stdout)
