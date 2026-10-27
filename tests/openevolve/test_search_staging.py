"""Real dataset prefix staging; no process launch or financial-result claim."""

import hashlib
import inspect
import json
from datetime import date

import pytest
from pydantic import ValidationError

from packages.openevolve_adapter.contracts import DevelopmentWindow, InputFile
from packages.openevolve_adapter.execution_inputs import (
    ResolvedNativeInputs,
    seeded_rng_state,
)
from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.config import (
    MarketConfig,
    WarmupConfig,
    canonical_bytes,
)
from packages.tradegrad_adapter.native_contract import NativeStrategyContract
from packages.tradegrad_adapter.windows import _stage_resolved_dataset_window
from tests.openevolve.test_contracts import complete_spec
from tests.tradegrad.test_config_windows import _bundle
from workers.openevolve.staging import calendar_projection, stage_search_input


def fixture(tmp_path):
    legacy, manifest, files = _bundle()
    manifest["price_semantics"] = {"adjustment": "unadjusted", "currency": "CNY"}
    inputs = ResolvedNativeInputs(
        task="cn_cross",
        universe_id=legacy.universe.id,
        market_profile=MarketConfig.for_market("CN"),
        warmup=WarmupConfig(sessions=3),
        strategy_contract=NativeStrategyContract(
            lookback_bars=3, warmup_bars=3, requires_shorting=False
        ),
    )
    window = DevelopmentWindow(
        id="dev",
        start=date(2024, 1, 5),
        end=date(2024, 1, 8),
        weight_millionths=1000000,
    )
    plan = complete_spec().execution.model_copy(
        update={
            "dataset_sha256": hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
            "universe_sha256": legacy.universe.sha256,
            "files": tuple(
                InputFile(
                    logical_name=f"input_{i}",
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                )
                for i, raw in enumerate(files.values())
            ),
            "engine_config_sha256": digest(inputs.execution_projection()),
            "parameter_sha256": digest([]),
            "state_schema_sha256": digest({"state_schema": inputs.state_schema}),
            "capture_profile_sha256": digest(inputs.capture.materialize()),
            "listing_map_sha256": digest(manifest["evaluator_symbol_mapping"]),
            "calendar_sha256": digest(calendar_projection(manifest)),
            "price_semantics_sha256": digest(manifest["price_semantics"]),
            "market": "CN",
            "warmup_sessions": 3,
            "windows": (window,),
        }
    )
    for name, raw in files.items():
        path = tmp_path / "store" / "dataset" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    source = b"x = 1\n"
    return {
        "plan": plan,
        "inputs": inputs,
        "window": window,
        "manifest": manifest,
        "storage_root": tmp_path / "store",
        "dataset_storage_key": "dataset",
        "source": source,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "target": tmp_path / "out",
        "max_bytes": 1048576,
    }


def test_real_prefix_warmup_no_future_listing_or_labels(tmp_path):
    args = fixture(tmp_path)
    stage_search_input(**args)
    target = args["target"]
    assert not (target / "data/B.csv").exists()
    assert not (target / "universe/members_list_2024-01-10.csv").exists()
    text = (target / "data/A.csv").read_text()
    assert "2024-01-02" in text and "2024-01-07" in text
    assert "2024-01-01" not in text and "2024-01-08" not in text
    request = json.loads((target / "request.json").read_bytes())
    assert request["evaluation_config"]["result_policy"] == "independent_observation.v1"
    assert request["evaluation_config"]["run_config_sha256"] == digest(args["plan"])
    assert request["runtime_observation"]["rng_restore"] == seeded_rng_state(42)
    assert "objective" not in request and "score" not in request
    assert (target / "strategy.py").read_bytes() == args["source"]


@pytest.mark.parametrize(
    "field",
    [
        "engine_config_sha256",
        "capture_profile_sha256",
        "parameter_sha256",
        "listing_map_sha256",
        "calendar_sha256",
        "price_semantics_sha256",
    ],
)
def test_staging_rejects_unresolved_profile_before_writes(tmp_path, field):
    args = fixture(tmp_path)
    args["plan"] = args["plan"].model_copy(update={field: "0" * 64})
    with pytest.raises(ValueError):
        stage_search_input(**args)
    assert not args["target"].exists()


def test_private_mechanic_signature_pin():
    assert tuple(inspect.signature(_stage_resolved_dataset_window).parameters) == (
        "config",
        "split",
        "window",
        "manifest",
        "read_file",
        "limits",
    )


def test_nested_native_bool_not_coerced(tmp_path):
    data = fixture(tmp_path)["inputs"].model_dump(mode="json")
    data["market_profile"]["t_plus_one"] = 1
    with pytest.raises(ValidationError):
        ResolvedNativeInputs.model_validate(data)


def test_unadmitted_window_fails(tmp_path):
    args = fixture(tmp_path)
    args["window"] = args["window"].model_copy(update={"end": date(2024, 1, 12)})
    with pytest.raises(ValueError):
        stage_search_input(**args)
    assert not args["target"].exists()
