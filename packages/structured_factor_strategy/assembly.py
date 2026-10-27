"""Deterministic declared-input assembly; callers supply freshly resolved evidence."""

import hashlib
from uuid import UUID

from packages.factors.contracts import digest
from packages.strategy_sdk import (
    DataRequirements,
    EngineProfile,
    PriceAssumptions,
    StateCodec,
    StrategyManifest,
    UniverseAssumptions,
    hash_json,
)
from packages.structured_factor_strategy.contracts import STATE, StructuredEngineInput
from runtimes.portable_engine.runtime import reviewed_profile


def assemble_input(
    *, fence, execution_id, config, program, snapshot, rows, source, market
):
    if (
        snapshot.input.dataset != source.dataset
        or snapshot.input.calculation_universe != source.universe
        or snapshot.input.calendar_sha256 != source.calendar.sha256
        or snapshot.input.market_timezone != source.calendar.timezone
    ):
        raise ValueError(
            "Completed factor snapshot differs from retained market source"
        )
    if (config.dataset_version_id, config.dataset_sha256, config.program_sha256) != (
        source.dataset.dataset_version_id,
        source.dataset.content_sha256,
        digest(program),
    ) or snapshot.snapshot_id not in config.snapshots:
        raise ValueError("Execution is outside immutable strategy declaration")
    native_ids = tuple(str(x.listing_id) for x in market.listings)
    if set(native_ids) != set(source.universe.listing_ids):
        raise ValueError("No symbol-based or inferred listing mapping")
    market_sha = hashlib.sha256(market.content).hexdigest()
    adapter = StrategyManifest(
        strategy_kind="portfolio",
        strategy_id=str(config.structured_strategy_version_id),
        code_sha256=digest(program),
        parameters_sha256=digest(program),
        market="US",
        currency="USD",
        calendar="XNYS",
        timezone="America/New_York",
        data=DataRequirements(
            lookback_bars=1, warmup_bars=1, max_listings=len(native_ids)
        ),
        universe=UniverseAssumptions(
            semantics="user_fixed",
            snapshot_sha256=hash_json(
                [x.model_dump(mode="json") for x in market.listings]
            ),
            survivorship_bias="possible",
        ),
        prices=PriceAssumptions(actions_snapshot_sha256=hash_json([])),
        state_schema=STATE,
        engine_profile=EngineProfile(
            profile_sha256=hash_json(reviewed_profile("US", config.initial_cash)),
            environment_sha256=digest(
                {
                    "image": config.engine_image_id,
                    "parent": config.engine_installed_parent_image_id,
                    "lock": config.engine_dependency_lock_sha256,
                }
            ),
        ),
    )
    return StructuredEngineInput(
        **{
            k: getattr(fence, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        },
        execution_id=execution_id,
        structured_strategy_version_id=config.structured_strategy_version_id,
        program=program,
        snapshot=snapshot,
        rows=rows,
        listing_bindings=tuple(
            {"factor_listing_id": key, "native_listing_id": UUID(key)}
            for key in sorted(native_ids)
        ),
        market_sha256=market_sha,
        engine={
            "manifest": adapter,
            "window": config.execution_window,
            "initial_cash": config.initial_cash,
            "initial_state": StateCodec(STATE).encode({}),
            "input_identity": {
                "run_config_sha256": digest(program),
                "market_data_sha256": market_sha,
            },
        },
    )
