"""Maintained real native strategy/staging fixture; no persisted admission grant."""

import hashlib
from datetime import date
from pathlib import Path

from packages.openevolve_adapter.contracts import DevelopmentWindow, InputFile
from packages.openevolve_adapter.execution_inputs import (
    ResolvedNativeInputs,
)
from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.config import (
    MarketConfig,
    WarmupConfig,
    canonical_bytes,
)
from packages.tradegrad_adapter.native_contract import NativeStrategyContract
from runtimes.openevolve_evaluator.contracts import check_input_bytes
from tests.openevolve.test_contracts import complete_spec
from tests.tradegrad.test_config_windows import _bundle
from workers.openevolve.input_inventory import inventory_staged_input
from workers.openevolve.staging import calendar_projection, stage_search_input


def stage_maintained_native(tmp_path, *, evaluation_id, evaluator_image):
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
    source = (
        Path(__file__).parents[2] / "seed_strategies/research_cross.py"
    ).read_bytes()
    plan = type(plan).model_validate(
        plan.model_copy(update={"evaluator_image": evaluator_image})
    )
    args = {
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

    stage_search_input(**args)
    retained = inventory_staged_input(
        args["target"],
        evaluation_id=evaluation_id,
        plan=plan,
        source_sha256=args["source_sha256"],
        window=window,
    )
    files = {
        entry.destination: (args["target"] / entry.path).read_bytes()
        for entry in retained.files
    }
    check_input_bytes(
        retained, {path.removeprefix("/input/"): raw for path, raw in files.items()}
    )
    return plan, retained, files
