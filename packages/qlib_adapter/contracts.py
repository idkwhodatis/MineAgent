"""Distinct direct-factor, native-prediction and portfolio evidence contracts."""

import math
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import Contract, DateWindow, Sha256, aware_utc, digest

QLIB_COMMIT = "54355232463878d2eebb91fe0ee5fa7fa1f5976c"


class LabelRow(Contract):
    session: date
    listing_id: str = Field(min_length=1, max_length=200)
    horizon_end: date
    horizon_end_time: datetime
    available_at: datetime
    value: float | None

    _time = field_validator("available_at", "horizon_end_time")(aware_utc)

    @field_validator("value", mode="before")
    @classmethod
    def exact_numeric(cls, value):
        if value is not None and type(value) not in (int, float):
            raise ValueError("Label must be finite numeric or explicitly missing")
        return value

    @model_validator(mode="after")
    def future_label(self):
        if self.horizon_end <= self.session:
            raise ValueError("Forward label must end after its observation session")
        if self.available_at < self.horizon_end_time:
            raise ValueError("Label availability precedes horizon completion")
        return self


class LabelManifest(Contract):
    schema_version: Literal["mineagent.forward-label-manifest.v1"] = (
        "mineagent.forward-label-manifest.v1"
    )
    dataset_id: UUID
    dataset_sha256: Sha256
    calendar_sha256: Sha256
    calendar_sessions_sha256: Sha256
    market_timezone: str = Field(min_length=1, max_length=64)
    universe_sha256: Sha256
    rows_sha256: Sha256
    row_count: int = Field(strict=True, ge=0, le=1_000_000)
    horizon_sessions: int = Field(strict=True, ge=1, le=4096)
    alignment: Literal["feature_session_to_forward_close_return"] = (
        "feature_session_to_forward_close_return"
    )
    unit: Literal["fractional_return"] = "fractional_return"
    price_semantics: Literal["unadjusted", "split_adjusted", "total_return"]
    source_claim: Literal["declared_not_independently_verified"] = (
        "declared_not_independently_verified"
    )

    @field_validator("market_timezone")
    @classmethod
    def known_timezone(cls, value):
        ZoneInfo(value)
        return value

    @property
    def sha256(self):
        return digest(self)


class DirectProtocol(Contract):
    schema_version: Literal["mineagent.factor-analysis-protocol.v2"] = (
        "mineagent.factor-analysis-protocol.v2"
    )
    kind: Literal["direct_factor_analysis"] = "direct_factor_analysis"
    snapshot_id: UUID
    snapshot_output_sha256: Sha256
    factor_points_sha256: Sha256
    label_manifest: LabelManifest
    window: DateWindow
    listing_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    qlib_commit: Literal["54355232463878d2eebb91fe0ee5fa7fa1f5976c"] = QLIB_COMMIT
    estimator: Literal["qlib.contrib.eva.alpha.calc_ic"] = (
        "qlib.contrib.eva.alpha.calc_ic"
    )
    missing_policy: Literal["pairwise_valid"] = "pairwise_valid"
    constant_policy: Literal["undefined_not_zero"] = "undefined_not_zero"
    aggregation: Literal["arithmetic_mean_of_defined_days"] = (
        "arithmetic_mean_of_defined_days"
    )
    preprocessing_policy: Literal["use_frozen_snapshot_no_refit"] = (
        "use_frozen_snapshot_no_refit"
    )
    return_aggregation: Literal["not_applicable"] = "not_applicable"

    @model_validator(mode="after")
    def unique(self):
        if tuple(sorted(set(self.listing_ids))) != self.listing_ids:
            raise ValueError("Listing identities must be sorted and unique")
        return self

    @property
    def sha256(self):
        return digest(self)


class FactorPoint(Contract):
    session: date
    listing_id: str = Field(min_length=1, max_length=200)
    value: float | None

    @field_validator("value", mode="before")
    @classmethod
    def exact_numeric(cls, value):
        if value is not None and type(value) not in (int, float):
            raise ValueError("Factor must be finite numeric or explicitly missing")
        return value


Correlation = Annotated[float, Field(strict=True, ge=-1, le=1)]


class DirectDay(Contract):
    session: date
    pair_count: int = Field(strict=True, ge=0, le=10_000)
    ic: Correlation | None
    rank_ic: Correlation | None
    reason: Literal["defined", "insufficient_pairs", "constant_cross_section"]

    @model_validator(mode="after")
    def meaning(self):
        if self.reason == "defined":
            if self.pair_count < 2 or self.ic is None or self.rank_ic is None:
                raise ValueError("Defined correlation requires paired observations")
        elif (self.reason == "insufficient_pairs" and self.pair_count >= 2) or (
            self.reason == "constant_cross_section" and self.pair_count < 2
        ):
            raise ValueError("Undefined correlation reason differs from pair count")
        elif self.ic is not None or self.rank_ic is not None:
            raise ValueError("Undefined correlation cannot be zero-filled")
        return self


class DirectResult(Contract):
    schema_version: Literal["mineagent.qlib-direct-series.v1"] = (
        "mineagent.qlib-direct-series.v1"
    )
    protocol_sha256: Sha256
    kind: Literal["direct_factor_analysis"] = "direct_factor_analysis"
    days: tuple[DirectDay, ...] = Field(min_length=1, max_length=4096)
    ic: Correlation | None
    rank_ic: Correlation | None
    defined_days: int = Field(strict=True, ge=0, le=4096)
    total_pairs: int = Field(strict=True, ge=0, le=1_000_000)
    cumulative_return: None = None

    @model_validator(mode="after")
    def closes(self):
        if [d.session for d in self.days] != sorted({d.session for d in self.days}):
            raise ValueError("Analysis dates must be unique and sorted")
        valid = [d for d in self.days if d.reason == "defined"]
        expected_ic = math.fsum(d.ic for d in valid) / len(valid) if valid else None
        expected_rank = (
            math.fsum(d.rank_ic for d in valid) / len(valid) if valid else None
        )
        if (
            self.defined_days != len(valid)
            or self.total_pairs != sum(d.pair_count for d in self.days)
            or self.ic != expected_ic
            or self.rank_ic != expected_rank
        ):
            raise ValueError("Analysis aggregate does not close its daily series")
        return self


class NativeBinding(Contract):
    schema_version: Literal["mineagent.qlib-native-analysis-binding.v1"] = (
        "mineagent.qlib-native-analysis-binding.v1"
    )
    owner_id: UUID
    dataset_id: UUID
    dataset_sha256: Sha256
    label_manifest_sha256: Sha256
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    factor_snapshot_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    combined_input_sha256: Sha256
    actual_learner_input_sha256: Sha256
    model_config_sha256: Sha256
    model_text_sha256: Sha256
    prediction_sha256: Sha256
    baseline_receipt_sha256: Sha256
    daily_report_sha256: Sha256
    fit_selection_sha256: Sha256
    training_fit_cutoff: datetime
    validation_selection_cutoff: datetime

    _cutoffs = field_validator("training_fit_cutoff", "validation_selection_cutoff")(
        aware_utc
    )
    training_window: DateWindow
    validation_window: DateWindow
    test_window: DateWindow
    model: Literal["qlib.contrib.model.gbdt.LGBModel"] = (
        "qlib.contrib.model.gbdt.LGBModel"
    )
    strategy: Literal["qlib.contrib.strategy.signal_strategy.TopkDropoutStrategy"] = (
        "qlib.contrib.strategy.signal_strategy.TopkDropoutStrategy"
    )
    qlib_commit: Literal["54355232463878d2eebb91fe0ee5fa7fa1f5976c"] = QLIB_COMMIT

    @model_validator(mode="after")
    def no_leak(self):
        if not (
            self.training_window.end
            <= self.validation_window.start
            < self.validation_window.end
            <= self.test_window.start
        ):
            raise ValueError("Native train/validation/test windows must not overlap")
        if self.training_fit_cutoff >= self.validation_selection_cutoff:
            raise ValueError("Native label selection cutoffs must be ordered")
        if len(set(self.factor_snapshot_ids)) != len(self.factor_snapshot_ids):
            raise ValueError("Duplicate factor snapshot binding")
        return self


class GroupDay(Contract):
    session: date
    pair_count: int = Field(strict=True, ge=0, le=10_000)
    long_short_label_return: Annotated[float, Field(strict=True)] | None
    universe_label_return: Annotated[float, Field(strict=True)] | None
    reason: Literal["defined", "insufficient_pairs", "constant_score"]

    @model_validator(mode="after")
    def meaning(self):
        values = (self.long_short_label_return, self.universe_label_return)
        if self.reason == "defined":
            if self.pair_count < 2 or any(v is None for v in values):
                raise ValueError("Group returns require two valid pairs")
        elif any(v is not None for v in values):
            raise ValueError("Undefined group return must remain missing")
        return self


class GroupResult(Contract):
    schema_version: Literal["mineagent.qlib-label-group-series.v1"] = (
        "mineagent.qlib-label-group-series.v1"
    )
    kind: Literal["factor_label_group_analysis"] = "factor_label_group_analysis"
    direct_protocol_sha256: Sha256
    estimator: Literal["qlib.contrib.eva.alpha.calc_long_short_return"] = (
        "qlib.contrib.eva.alpha.calc_long_short_return"
    )
    quantile: Literal[0.5] = 0.5
    tie_policy: Literal["qlib_nlargest_nsmallest_first_sorted_listing"] = (
        "qlib_nlargest_nsmallest_first_sorted_listing"
    )
    semantics: Literal["half_top_minus_bottom_forward_label_mean_no_costs"] = (
        "half_top_minus_bottom_forward_label_mean_no_costs"
    )
    aggregation: Literal["daily_series_only"] = "daily_series_only"
    days: tuple[GroupDay, ...] = Field(min_length=1, max_length=4096)
    cumulative_return: None = None

    @model_validator(mode="after")
    def ordered(self):
        if [d.session for d in self.days] != sorted({d.session for d in self.days}):
            raise ValueError("Group dates must be sorted and unique")
        return self


class NativeDay(Contract):
    session: date
    gross_return: float = Field(strict=True, ge=-1)
    cost: float = Field(strict=True, ge=0)
    turnover: float = Field(strict=True, ge=0)
    account_value: float = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def solvent(self):
        if self.gross_return - self.cost < -1:
            raise ValueError("Net daily return cannot be below total loss")
        return self


class NativePortfolioResult(Contract):
    schema_version: Literal["mineagent.qlib-native-portfolio.v1"] = (
        "mineagent.qlib-native-portfolio.v1"
    )
    kind: Literal["native_qlib_portfolio_analysis"] = "native_qlib_portfolio_analysis"
    native_binding_sha256: Sha256
    daily_report_sha256: Sha256
    days: tuple[NativeDay, ...] = Field(min_length=1, max_length=4096)
    aggregation: Literal["compound_daily_gross_minus_cost"] = (
        "compound_daily_gross_minus_cost"
    )
    net_return_compound: float = Field(strict=True, ge=-1)
    turnover_arithmetic_sum: float = Field(strict=True, ge=0)
    financial_claim: Literal[
        "qlib_native_simulation_not_akquant_strategy_or_factor_incremental_benefit"
    ] = "qlib_native_simulation_not_akquant_strategy_or_factor_incremental_benefit"

    @model_validator(mode="after")
    def closes(self):
        if [d.session for d in self.days] != sorted({d.session for d in self.days}):
            raise ValueError("Native portfolio sessions must be sorted and unique")
        net = math.prod(1 + d.gross_return - d.cost for d in self.days) - 1
        turnover = math.fsum(d.turnover for d in self.days)
        if not math.isclose(
            net, self.net_return_compound, rel_tol=1e-12, abs_tol=1e-12
        ) or not math.isclose(
            turnover, self.turnover_arithmetic_sum, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError(
                "Native portfolio aggregate differs from actual daily series"
            )
        return self


NativeArtifactRole = Literal[
    "configuration",
    "model_text",
    "prediction",
    "combined_input",
    "learner_input",
    "daily_report",
    "baseline_receipt",
    "fit_selection",
]


class NativeArtifact(Contract):
    role: NativeArtifactRole
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=1, le=64 * 1024 * 1024)
    media_type: Literal[
        "application/json", "text/plain", "application/vnd.apache.parquet"
    ]

    @model_validator(mode="after")
    def role_media(self):
        expected = (
            "text/plain"
            if self.role == "model_text"
            else "application/json"
            if self.role in ("configuration", "baseline_receipt", "fit_selection")
            else "application/vnd.apache.parquet"
        )
        if self.media_type != expected:
            raise ValueError("Native artifact media differs from its closed role")
        return self


class NativeEvidenceManifest(Contract):
    schema_version: Literal["mineagent.qlib-native-evidence.v1"] = (
        "mineagent.qlib-native-evidence.v1"
    )
    binding: NativeBinding
    artifacts: tuple[NativeArtifact, ...] = Field(min_length=8, max_length=8)
    artifact_loading: Literal["data_only_never_deserialize_python_objects"] = (
        "data_only_never_deserialize_python_objects"
    )

    @model_validator(mode="after")
    def closure(self):
        artifacts = {a.role: a for a in self.artifacts}
        fields = {
            "configuration": "model_config_sha256",
            "model_text": "model_text_sha256",
            "prediction": "prediction_sha256",
            "combined_input": "combined_input_sha256",
            "learner_input": "actual_learner_input_sha256",
            "baseline_receipt": "baseline_receipt_sha256",
            "daily_report": "daily_report_sha256",
            "fit_selection": "fit_selection_sha256",
        }
        if len(artifacts) != 8 or set(artifacts) != set(fields):
            raise ValueError(
                "Native evidence closure must contain each artifact exactly once"
            )
        for role, field in fields.items():
            if artifacts[role].sha256 != getattr(self.binding, field):
                raise ValueError("Native evidence hash binding differs")
        return self


class NativePredictionResult(Contract):
    schema_version: Literal["mineagent.qlib-native-prediction-analysis.v1"] = (
        "mineagent.qlib-native-prediction-analysis.v1"
    )
    kind: Literal["native_prediction_analysis"] = "native_prediction_analysis"
    native_binding_sha256: Sha256
    days: tuple[DirectDay, ...] = Field(min_length=1, max_length=4096)
    ic: Correlation | None
    rank_ic: Correlation | None
    defined_days: int = Field(strict=True, ge=0, le=4096)
    total_pairs: int = Field(strict=True, ge=0, le=1_000_000)
    aggregation: Literal["arithmetic_mean_of_defined_days"] = (
        "arithmetic_mean_of_defined_days"
    )
    cumulative_return: None = None

    @model_validator(mode="after")
    def closes(self):
        # Reuse the daily-series closure, without reusing its result namespace.
        DirectResult(
            protocol_sha256=self.native_binding_sha256,
            days=self.days,
            ic=self.ic,
            rank_ic=self.rank_ic,
            defined_days=self.defined_days,
            total_pairs=self.total_pairs,
        )
        return self
