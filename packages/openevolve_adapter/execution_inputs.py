"""Resolved immutable native inputs, still not process admission authority."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.research_runtime.broker_contracts import Contract, digest
from packages.research_trace.contracts import CaptureConfig
from packages.tradegrad_adapter.config import MarketConfig, WarmupConfig
from packages.tradegrad_adapter.native_contract import NativeStrategyContract

from .contracts import StrategyExecutionPlan


class CaptureSettings(Contract):
    mode: Literal["summary"] = "summary"
    max_decisions: int = Field(default=10000, ge=1, le=10000)
    max_detail_rows: int = Field(default=10000, ge=1, le=10000)
    max_uncompressed_bytes: int = Field(default=1048576, ge=1024, le=1048576)

    def materialize(self):
        return CaptureConfig(**self.model_dump()).model_dump(mode="json")


class ResolvedNativeInputs(Contract):
    schema_version: Literal["mineagent.resolved-native-inputs.v1"] = (
        "mineagent.resolved-native-inputs.v1"
    )
    task: Literal["cn_cross", "cn_time", "us_cross", "us_time"]
    universe_id: str = Field(min_length=1, max_length=128)
    market_profile: MarketConfig
    warmup: WarmupConfig
    strategy_contract: NativeStrategyContract
    capture: CaptureSettings = CaptureSettings()
    state_schema: Literal["native_state_unobservable.v1"] = (
        "native_state_unobservable.v1"
    )

    @field_validator("market_profile", "warmup", "strategy_contract", mode="before")
    @classmethod
    def strict_nested(cls, value, info):
        model = {
            "market_profile": MarketConfig,
            "warmup": WarmupConfig,
            "strategy_contract": NativeStrategyContract,
        }[info.field_name]
        if isinstance(value, model):
            return model.model_validate_json(value.model_dump_json(), strict=True)
        return model.model_validate(value, strict=True)

    @model_validator(mode="after")
    def coherent(self):
        # Revalidate nested legacy DTOs strictly rather than inherit their
        # coercion or accept unchecked model_copy instances as execution input.
        MarketConfig.model_validate_json(
            self.market_profile.model_dump_json(), strict=True
        )
        WarmupConfig.model_validate_json(self.warmup.model_dump_json(), strict=True)
        NativeStrategyContract.model_validate_json(
            self.strategy_contract.model_dump_json(), strict=True
        )
        if not self.task.startswith(self.market_profile.market.lower() + "_"):
            raise ValueError("Task and native market disagree")
        if self.warmup.sessions < max(
            self.strategy_contract.lookback_bars, self.strategy_contract.warmup_bars
        ):
            raise ValueError("Warmup does not satisfy declared native contract")
        if self.warmup.history_policy == "source_cold_start" and self.warmup.sessions:
            raise ValueError("Cold start cannot claim staged warmup")
        if self.strategy_contract.requires_shorting and not (
            self.market_profile.risk_config
            and self.market_profile.risk_config.enable_short_sell
        ):
            raise ValueError("Market does not support declared shorting")
        return self

    def execution_projection(self):
        return {
            "task": self.task,
            "market_profile": self.market_profile.model_dump(mode="json"),
            "warmup": self.warmup.model_dump(mode="json"),
            "strategy_contract": self.strategy_contract.model_dump(mode="json"),
        }

    def validate_plan(self, plan: StrategyExecutionPlan):
        if type(plan) is not StrategyExecutionPlan:
            raise ValueError("Typed execution plan required")
        expected = {
            "engine_config_sha256": digest(self.execution_projection()),
            "parameter_sha256": digest(
                [p.model_dump(mode="json") for p in self.strategy_contract.parameters]
            ),
            "state_schema_sha256": digest({"state_schema": self.state_schema}),
            "capture_profile_sha256": digest(self.capture.materialize()),
        }
        if any(getattr(plan, key) != value for key, value in expected.items()):
            raise ValueError("Resolved native inputs differ from frozen plan")
        if (
            plan.market != self.market_profile.market
            or plan.warmup_sessions != self.warmup.sessions
        ):
            raise ValueError("Native execution window profile differs")


def seeded_rng_state(seed):
    """Detached generators only; neither ambient generator is changed.

    This binds the supported Python/NumPy legacy seam, not arbitrary candidate
    RNGs, process hash seeds, OS entropy or strategy self-reseeding.
    """
    import random

    from packages.tradegrad_adapter.runtime_observation import (
        RNG_VERSION,
        UNCOVERED,
        validate_rng_state,
    )

    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid frozen RNG seed")
    py = random.Random(seed).getstate()
    # NumPy RandomState(seed) uses MT19937 init_genrand, without drawing.
    # Keep the host free of a NumPy dependency; differential tests compare all
    # 624 words against the pinned evaluator NumPy implementation.
    keys = [seed]
    for index in range(1, 624):
        previous = keys[-1]
        keys.append((1812433253 * (previous ^ (previous >> 30)) + index) & 0xFFFFFFFF)
    return validate_rng_state(
        {
            "schema_version": RNG_VERSION,
            "seam": "before_candidate_import.v1",
            "python": {"version": py[0], "state": list(py[1]), "gauss_next": py[2]},
            "numpy_legacy": {
                "algorithm": "MT19937",
                "keys": keys,
                "position": 624,
                "has_gauss": 0,
                "cached_gaussian": 0.0,
            },
            "scope": ["python.random", "numpy.random.legacy"],
            "uncovered": list(UNCOVERED),
        },
        include_numpy=True,
    )
