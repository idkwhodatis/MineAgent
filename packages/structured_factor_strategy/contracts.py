"""Closed025 input grammar; parsing data never grants launch/publication authority.

The embedded portable SDK projection is an adapter input only. A structured
program is NOT portable policy source and may not inherit portable trust claims.
"""

from datetime import timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import (
    Contract,
    DateWindow,
    FeatureRow,
    Sha256,
    digest,
    rows_digest,
    validate_rows,
)
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.strategy_sdk import (
    StateCodec,
    StateSchema,
    StateSnapshot,
    StrategyManifest,
    hash_json,
)

PROFILE = "mineagent.factor-strategy-engine.v1"
MAX_INPUT_BYTES = 1_048_576
MAX_MARKET_BYTES = 33_554_432
MAX_RESULT_BYTES = 16_777_216
MAX_DECISIONS = 8
STATE = StateSchema(name="structured-factor-stateless", version=1, fields=())


class StructuredProgram(Contract):
    schema_version: Literal["mineagent.structured-factor-strategy.v1"] = (
        "mineagent.structured-factor-strategy.v1"
    )
    signal_window: DateWindow
    eligibility_operator: Literal["valid_factor_strictly_above_threshold"] = (
        "valid_factor_strictly_above_threshold"
    )
    threshold: float = Field(strict=True, ge=-1e15, le=1e15)
    score_operator: Literal["identity_factor_value_descending"] = (
        "identity_factor_value_descending"
    )
    allocation_operator: Literal["top_k_equal_capped_floor12"] = (
        "top_k_equal_capped_floor12"
    )
    top_k: int = Field(strict=True, ge=1, le=100)
    maximum_weight: float = Field(strict=True, gt=0, le=1)
    maximum_gross_weight: float = Field(strict=True, gt=0, le=1)
    missing_value_policy: Literal["exclude_without_substitution"] = (
        "exclude_without_substitution"
    )
    empty_eligible_policy: Literal["explicit_liquidate"] = "explicit_liquidate"
    outside_signal_window: Literal["hold_without_factor_read"] = (
        "hold_without_factor_read"
    )
    tie_break: Literal["native_listing_uuid_ascending"] = (
        "native_listing_uuid_ascending"
    )

    @model_validator(mode="after")
    def signal_window_bound(self):
        if self.signal_window.end - self.signal_window.start != timedelta(days=1):
            raise ValueError(
                "Initial structured profile requires one declared signal day"
            )
        return self


class ListingBinding(Contract):
    factor_listing_id: str = Field(min_length=1, max_length=200)
    native_listing_id: UUID


class EngineIdentity(Contract):
    run_config_sha256: Sha256
    market_data_sha256: Sha256


class EngineProjection(Contract):
    schema_version: Literal["portable-engine-request.v1"] = "portable-engine-request.v1"
    manifest: StrategyManifest
    window: DateWindow
    initial_cash: float = Field(strict=True, gt=0, le=1e12)
    initial_state: StateSnapshot
    input_identity: EngineIdentity

    @model_validator(mode="after")
    def no_policy(self):
        state = StateSnapshot.model_validate(self.initial_state)
        if StateCodec(STATE).decode(state) != {} or self.manifest.state_schema != STATE:
            raise ValueError("Structured program has no caller state or policy")
        if (
            self.manifest.authoring is not None
            or self.manifest.reference_result_sha256 is not None
        ):
            raise ValueError(
                "Portable policy declarations cannot enter structured execution"
            )
        if self.manifest.strategy_kind != "portfolio" or self.manifest.market != "US":
            raise ValueError(
                "Initial structured profile is reviewed US daily portfolio only"
            )
        if self.manifest.data.lookback_bars != 1 or self.manifest.data.warmup_bars != 1:
            raise ValueError(
                "Structured interpreter uses only current trusted observation"
            )
        return self


class StructuredEngineInput(Contract):
    schema_version: Literal["mineagent.structured-factor-engine-input.v1"] = (
        "mineagent.structured-factor-engine-input.v1"
    )
    profile: Literal["mineagent.factor-strategy-engine.v1"] = PROFILE
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    execution_id: UUID
    structured_strategy_version_id: UUID
    program: StructuredProgram
    snapshot: ExecutionSnapshotManifest
    rows: tuple[FeatureRow, ...] = Field(min_length=1, max_length=10000)
    listing_bindings: tuple[ListingBinding, ...] = Field(min_length=1, max_length=100)
    market_sha256: Sha256
    engine: EngineProjection

    @model_validator(mode="after")
    def bindings(self):
        manifest = self.snapshot
        validate_rows(manifest.input, self.rows)
        if (
            manifest.owner_id != self.owner_id
            or rows_digest(self.rows) != manifest.output_rows_sha256
            or len(self.rows) != manifest.row_count
        ):
            raise ValueError("Exact completed snapshot row identity differs")
        if (
            len({(row.session_date, row.listing_id) for row in self.rows})
            != len(self.rows)
            or len(self.rows) != len(self.listing_bindings)
            or sum(row.value_state == "valid" for row in self.rows)
            != manifest.valid_count
        ):
            raise ValueError(
                "Initial structured snapshot must be the complete one-day listing grid"
            )
        if manifest.input.window != self.program.signal_window:
            raise ValueError(
                "Initial strategy signal window must equal complete factor window"
            )
        pairs = tuple(
            (b.factor_listing_id, str(b.native_listing_id))
            for b in self.listing_bindings
        )
        if (
            tuple(sorted(pairs)) != pairs
            or len({p[0] for p in pairs}) != len(pairs)
            or len({p[1] for p in pairs}) != len(pairs)
        ):
            raise ValueError("Explicit listing map must be sorted and one-to-one")
        if {p[0] for p in pairs} != set(
            manifest.input.calculation_universe.listing_ids
        ):
            raise ValueError(
                "Listing map must cover the exact factor calculation universe"
            )
        engine = self.engine
        if (
            engine.manifest.strategy_id != str(self.structured_strategy_version_id)
            or engine.manifest.code_sha256 != digest(self.program)
            or engine.manifest.parameters_sha256 != digest(self.program)
        ):
            raise ValueError("SDK projection is not the exact closed program")
        if (
            engine.input_identity.market_data_sha256 != self.market_sha256
            or engine.input_identity.run_config_sha256 != digest(self.program)
        ):
            raise ValueError("Engine input commitment differs")
        if (
            engine.window.start != self.program.signal_window.start
            or not self.program.signal_window.end < engine.window.end
            or (engine.window.end - engine.window.start).days > 8
        ):
            raise ValueError(
                "Require signal day and bounded later fill/valuation session"
            )
        if engine.manifest.universe.semantics != "user_fixed":
            raise ValueError("No inferred historical/PIT universe")
        if hash_json(engine.manifest.state_schema) != hash_json(STATE):
            raise ValueError("State schema differs")
        return self
