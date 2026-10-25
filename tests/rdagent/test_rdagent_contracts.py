from uuid import UUID

import pytest

from packages.factors.fixtures import demo_fixture
from packages.rdagent_adapter.contracts import FactorResearchConfig


def research_dict():
    snapshot = demo_fixture(
        factor_version_id=UUID(int=1), dataset_version_id=UUID(int=2)
    ).manifest.input
    return {
        "input": {
            "dataset": snapshot.dataset.model_dump(mode="json"),
            "calculation_universe": snapshot.calculation_universe.model_dump(
                mode="json"
            ),
            "window": snapshot.window.model_dump(mode="json"),
            "calendar_sha256": snapshot.calendar_sha256,
            "market_timezone": snapshot.market_timezone,
            "input_fields": [
                x.model_dump(mode="json") for x in snapshot.definition.input_fields
            ],
            "lookback_sessions": 2,
            "lag_sessions": 0,
        },
        "task": {
            "name": "momentum",
            "description": "Two-session momentum",
            "formulation": "close / previous close - 1",
            "factor_kind": "time_series",
            "output_unit": "return",
            "interpretation": "higher_preferred",
        },
        "runtime": {
            "dependency_locks": [
                {"role": r, "sha256": "a" * 64}
                for r in ("analysis", "candidate", "normalizer", "orchestrator")
            ],
            **{
                r + "_image": "sha256:" + "b" * 64
                for r in ("orchestrator", "candidate", "normalizer", "analysis")
            },
        },
        "budget": {
            "calls": 8,
            "tokens": 10000,
            "mutation_slots": 3,
            "evaluator_executions": 24,
            "wall_milliseconds": 600000,
            "storage_bytes": 10000000,
            "process_slots": 4,
        },
        "max_iterations": 3,
        "max_output_rows_per_cutoff": 3,
        "max_output_bytes_per_cutoff": 10000,
    }


def test_config_json_roundtrip_freezes_full_identity():
    config = FactorResearchConfig.model_validate(research_dict())
    assert FactorResearchConfig.model_validate_json(config.model_dump_json()) == config
    changed = config.model_dump(mode="json")
    changed["runtime"]["dependency_locks"][1]["sha256"] = "c" * 64
    assert FactorResearchConfig.model_validate(changed).sha256 != config.sha256
    with pytest.raises(ValueError):
        config.max_iterations = 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("upstream_workers", True),
        ("upstream_workers", 2),
        ("max_iterations", True),
        ("max_iterations", 9),
        ("max_source_bytes", 131072.0),
        ("model_profile", "openai"),
        ("base_feature_codes", ["evil"]),
        ("schema_version", "tradegrad.run.v1"),
    ],
)
def test_config_rejects_unsupported_or_coerced_limits(key, value):
    raw = research_dict()
    raw[key] = value
    with pytest.raises(ValueError):
        FactorResearchConfig.model_validate(raw)


@pytest.mark.parametrize(
    "change", ["label", "duplicate", "price", "lock", "budget", "universe"]
)
def test_config_rejects_incomplete_frozen_inputs(change):
    raw = research_dict()
    if change == "label":
        raw["input"]["input_fields"][0]["name"] = "forward_return"
    elif change == "duplicate":
        raw["input"]["input_fields"] *= 2
    elif change == "price":
        raw["input"]["dataset"]["price_semantics"] = "total_return"
    elif change == "lock":
        raw["runtime"]["dependency_locks"].pop()
    elif change == "budget":
        raw["budget"]["mutation_slots"] = 2
    elif change == "universe":
        raw["max_output_rows_per_cutoff"] = 2
    with pytest.raises(ValueError):
        FactorResearchConfig.model_validate(raw)
