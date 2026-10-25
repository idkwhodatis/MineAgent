"""Factor identities and host-bound temporal contracts.

Hashes prove integrity, not producer trust. Imported declarations cannot create
local verification authority; the trusted admission layer supplies that separately.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")
]
Count = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
SessionCount = Annotated[int, Field(strict=True, ge=0, le=4096)]
MAX_SOURCE_BYTES = 131_072
MAX_METADATA_BYTES = 1_048_576


def source_sha256(code: str) -> str:
    try:
        raw = code.encode("utf-8", errors="strict")
    except (AttributeError, UnicodeError) as exc:
        raise ValueError("Source must be UTF-8 text") from exc
    if not raw or len(raw) > MAX_SOURCE_BYTES or b"\0" in raw or not code.strip():
        raise ValueError("Source exceeds its byte contract or is empty")
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include its timezone")
    return value.astimezone(UTC)


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )

    @model_validator(mode="after")
    def bounded(self):
        if len(canonical_bytes(self)) > MAX_METADATA_BYTES:
            raise ValueError("Factor metadata exceeds its byte budget")
        return self


class DateWindow(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("Windows are nonempty, half-open [start,end)")
        return self


class Parameter(Contract):
    name: Identifier
    value: (
        Annotated[bool, Field(strict=True)]
        | Annotated[int, Field(strict=True)]
        | Annotated[float, Field(strict=True)]
        | Annotated[str, Field(strict=True, max_length=4000)]
    )


class InputField(Contract):
    name: Identifier
    unit: Identifier
    dtype: Literal["float64", "int64"] = "float64"
    price_semantics: Literal[
        "not_price", "unadjusted", "split_adjusted", "total_return"
    ]
    role: Literal["market_input"] = "market_input"

    @field_validator("name")
    @classmethod
    def no_label(cls, value):
        # A closed role prevents forward labels being part of computation input.
        if value.lower() in {"label", "target", "forward_return", "future_return"}:
            raise ValueError("Forward labels are analysis-only inputs")
        return value


class NoPreprocessing(Contract):
    kind: Literal["none"] = "none"


class FittedPreprocessing(Contract):
    kind: Literal["fitted"] = "fitted"
    transform_id: Identifier
    code_sha256: Sha256
    parameters_sha256: Sha256
    fitted_artifact_sha256: Sha256
    fit_window: DateWindow
    last_input_at: datetime
    available_at: datetime

    _times = field_validator("last_input_at", "available_at")(aware_utc)

    @model_validator(mode="after")
    def ready(self):
        if self.available_at < self.last_input_at:
            raise ValueError("Fitted transform precedes its required input")
        return self


Preprocessing = Annotated[
    NoPreprocessing | FittedPreprocessing, Field(discriminator="kind")
]


class FactorDefinition(Contract):
    schema_version: Literal["mineagent.factor-definition.v1"] = (
        "mineagent.factor-definition.v1"
    )
    code_sha256: Sha256
    factor_kind: Literal["cross_sectional", "time_series"]
    frequency: Literal["1d"] = "1d"
    input_fields: tuple[InputField, ...] = Field(min_length=1, max_length=32)
    lookback_sessions: SessionCount
    lag_sessions: SessionCount = 0
    calculation_universe_policy: Literal["frozen_universe", "dataset_inventory"] = (
        "dataset_inventory"
    )
    missing_policy: Literal["explicit_missing"] = "explicit_missing"
    parameters: tuple[Parameter, ...] = Field(default=(), max_length=32)
    preprocessing: Preprocessing = Field(default_factory=NoPreprocessing)
    output_unit: Identifier
    interpretation: Literal["higher_preferred", "lower_preferred", "unsigned"] = (
        "higher_preferred"
    )

    @model_validator(mode="after")
    def unique(self):
        for group in (self.input_fields, self.parameters):
            names = [item.name for item in group]
            if len(names) != len(set(names)):
                raise ValueError("Definition names must be unique")
        return self

    @property
    def sha256(self):
        return digest(self)

    def validate_source(self, code: str) -> None:
        if source_sha256(code) != self.code_sha256:
            raise ValueError("Exact source bytes differ from definition")


class FactorVersionRef(Contract):
    factor_version_id: UUID
    definition_sha256: Sha256
    code_sha256: Sha256


class DatasetBinding(Contract):
    dataset_version_id: UUID
    content_sha256: Sha256
    input_files_sha256: Sha256
    price_semantics: Literal["unadjusted", "split_adjusted", "total_return"]
    availability_revision_sha256: Sha256
    source_quality: Literal["declared_source", "unknown", "synthetic_fixture"] = (
        "unknown"
    )


class CalculationUniverse(Contract):
    universe_version_id: UUID | None = None
    content_sha256: Sha256
    listing_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=10_000)
    membership_sha256: Sha256
    membership_available_at: datetime
    membership_history: Literal["declared", "unknown", "synthetic_fixture"] = "unknown"

    _time = field_validator("membership_available_at")(aware_utc)

    @model_validator(mode="after")
    def unique(self):
        if tuple(sorted(set(self.listing_ids))) != self.listing_ids:
            raise ValueError("Calculation listings must be unique and sorted")
        return self


class ExposureClaim(Contract):
    origin_namespace: Identifier
    evidence_id: Identifier
    evidence_sha256: Sha256
    window: DateWindow | None
    purpose: Identifier
    identity_mapping: Literal["verified_local_mapping", "unresolved"] = "unresolved"
    mapping_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def mapping(self):
        if (self.identity_mapping == "verified_local_mapping") != (
            self.mapping_sha256 is not None
        ):
            raise ValueError("Mapped exposure requires exact mapping identity")
        return self


class ExposureHistory(Contract):
    claims: tuple[ExposureClaim, ...] = Field(default=(), max_length=256)
    completeness: Literal["unknown", "partial", "local_observed"] = "unknown"
    unresolved_reasons: tuple[Identifier, ...] = Field(default=(), max_length=32)

    @property
    def has_unknown_history(self):
        return (
            self.completeness != "local_observed"
            or bool(self.unresolved_reasons)
            or any(c.identity_mapping == "unresolved" for c in self.claims)
        )


def validate_history_inheritance(
    parent: ExposureHistory, child: ExposureHistory
) -> None:
    if parent.has_unknown_history and not child.has_unknown_history:
        raise ValueError("Binding cannot erase unknown inherited exposure")
    if not {digest(c) for c in parent.claims} <= {digest(c) for c in child.claims}:
        raise ValueError("Binding cannot omit inherited exposure claims")
    if not set(parent.unresolved_reasons) <= set(child.unresolved_reasons):
        raise ValueError("Binding cannot omit unresolved exposure reasons")


class LocalVerification(Contract):
    """Server-owned admission record; never accepted from package/client claims."""

    kind: Literal["declared_or_unknown", "fixed_fixture_recomputed", "verified_source"]
    admission_profile: Identifier
    evidence_sha256: Sha256
    source_pit_verified: bool = Field(strict=True, default=False)

    @model_validator(mode="after")
    def authority(self):
        if self.source_pit_verified != (self.kind == "verified_source"):
            raise ValueError("Fixture/import verification is not source PIT proof")
        return self


def imported_verification(*, evidence_sha256: str) -> LocalVerification:
    """An import can establish byte/schema admission, never upstream PIT truth."""
    return LocalVerification(
        kind="declared_or_unknown",
        admission_profile="isolated-import.v1",
        evidence_sha256=evidence_sha256,
    )


class SnapshotInput(Contract):
    schema_version: Literal["mineagent.feature-input.v1"] = "mineagent.feature-input.v1"
    version: FactorVersionRef
    definition: FactorDefinition
    dataset: DatasetBinding
    calculation_universe: CalculationUniverse
    window: DateWindow
    market_timezone: Identifier
    calendar_sha256: Sha256
    environment_sha256: Sha256
    adapter_version: Identifier
    materialization_profile: Identifier
    exposure_history: ExposureHistory = Field(default_factory=ExposureHistory)

    @field_validator("market_timezone")
    @classmethod
    def timezone(cls, value):
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def binding(self):
        if (
            self.version.definition_sha256 != self.definition.sha256
            or self.version.code_sha256 != self.definition.code_sha256
        ):
            raise ValueError("Snapshot definition/version binding differs")
        if (
            self.definition.calculation_universe_policy == "frozen_universe"
            and self.calculation_universe.universe_version_id is None
        ):
            raise ValueError("Frozen universe requires exact version")
        prices = {
            field.price_semantics
            for field in self.definition.input_fields
            if field.price_semantics != "not_price"
        }
        if prices and prices != {self.dataset.price_semantics}:
            raise ValueError("Input price semantics differ from dataset")
        return self

    @property
    def sha256(self):
        return digest(self)


ValueState = Literal[
    "valid",
    "warmup_insufficient",
    "input_missing",
    "zero_denominator",
    "calculation_error",
    "not_available",
]


class FeatureRow(Contract):
    event_time: datetime
    session_date: date
    listing_id: Identifier
    factor_version_id: UUID
    available_at: datetime
    value: float | None
    value_state: ValueState
    missing_reason: Identifier | None = None
    input_window_start: date
    input_window_end: date
    input_evidence_digest: Sha256
    # Readiness is independent of a producer's advertised available_at.
    required_input_available_at: datetime
    membership_available_at: datetime
    lag_available_at: datetime

    _times = field_validator(
        "event_time",
        "available_at",
        "required_input_available_at",
        "membership_available_at",
        "lag_available_at",
    )(aware_utc)

    @field_validator("value", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or (
            value is not None and not isinstance(value, (int, float))
        ):
            raise ValueError("Factor value must be a finite number or null")
        return value

    @model_validator(mode="after")
    def state(self):
        if (self.value_state == "valid") != (self.value is not None):
            raise ValueError("Only a valid observation has a numeric value")
        if (self.value_state == "valid") != (self.missing_reason is None):
            raise ValueError("Missing observations require an explicit reason")
        if self.input_window_start >= self.input_window_end:
            raise ValueError("Input window must be nonempty and half-open")
        if self.input_window_end > self.session_date + timedelta(days=1):
            raise ValueError("Input window contains future sessions")
        if self.available_at < max(
            self.required_input_available_at,
            self.membership_available_at,
            self.lag_available_at,
            self.event_time,
        ):
            raise ValueError("Availability precedes required temporal readiness")
        return self


class ObjectRef(Contract):
    role: Literal[
        "code",
        "values_partition",
        "calculation_samples",
        "analysis_series",
        "native_evidence",
    ]
    ordinal: Count
    sha256: Sha256
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=268_435_456)]
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    rows: Count = 0


class FeatureSnapshotManifest(Contract):
    schema_version: Literal["mineagent.feature-snapshot.v1"] = (
        "mineagent.feature-snapshot.v1"
    )
    snapshot_id: UUID
    input: SnapshotInput
    input_digest: Sha256
    output_digest: Sha256
    row_count: Count
    valid_count: Count
    missing_count: Count
    objects: tuple[ObjectRef, ...] = Field(default=(), max_length=4096)

    @model_validator(mode="after")
    def exact(self):
        if self.input_digest != self.input.sha256:
            raise ValueError("Snapshot input digest differs")
        if self.valid_count + self.missing_count != self.row_count:
            raise ValueError("Snapshot counts do not close")
        if len({(obj.role, obj.ordinal) for obj in self.objects}) != len(self.objects):
            raise ValueError("Duplicate snapshot object role/ordinal")
        if len({obj.name for obj in self.objects}) != len(self.objects):
            raise ValueError("Duplicate snapshot object name")
        return self

    @property
    def sha256(self):
        return digest(self)


def row_key(row: FeatureRow):
    return row.event_time, row.listing_id, str(row.factor_version_id)


def validate_rows(snapshot_input: SnapshotInput, rows: tuple[FeatureRow, ...]) -> None:
    """Semantic validation for already-decoded trusted rows, not a Parquet parser."""
    if len(rows) > 1_000_000:
        raise ValueError("Factor row budget exceeded")
    keys = tuple(row_key(row) for row in rows)
    if len(set(keys)) != len(keys) or tuple(sorted(keys)) != keys:
        raise ValueError("Factor rows must be unique and sorted")
    listings = set(snapshot_input.calculation_universe.listing_ids)
    fit = snapshot_input.definition.preprocessing
    for row in rows:
        FeatureRow.model_validate(row)
        if (
            row.factor_version_id != snapshot_input.version.factor_version_id
            or row.listing_id not in listings
        ):
            raise ValueError("Row identity differs from frozen factor/universe")
        if (
            not snapshot_input.window.start
            <= row.session_date
            < snapshot_input.window.end
        ):
            raise ValueError("Row lies outside frozen output window")
        if (
            row.event_time.astimezone(ZoneInfo(snapshot_input.market_timezone)).date()
            != row.session_date
        ):
            raise ValueError("Market session date differs from event timestamp")
        if (
            row.membership_available_at
            < snapshot_input.calculation_universe.membership_available_at
        ):
            raise ValueError("Row hides universe membership readiness")
        if isinstance(fit, FittedPreprocessing) and row.available_at < fit.available_at:
            raise ValueError("Row precedes fitted transform readiness")


def rows_digest(rows: tuple[FeatureRow, ...]) -> str:
    return digest([row.model_dump(mode="json") for row in rows])


class FactorBundle(Contract):
    schema_version: Literal["mineagent.factor-bundle.v1"] = "mineagent.factor-bundle.v1"
    bundle_id: UUID
    members: tuple[FactorVersionRef, ...] = Field(min_length=1, max_length=64)
    exposure_history: ExposureHistory = Field(default_factory=ExposureHistory)

    @model_validator(mode="after")
    def unique(self):
        if len({v.factor_version_id for v in self.members}) != len(self.members):
            raise ValueError("Duplicate bundle factor")
        return self

    @property
    def sha256(self):
        return digest(self)


class SnapshotBinding(Contract):
    version: FactorVersionRef
    snapshot_id: UUID
    input_digest: Sha256
    output_digest: Sha256
    manifest_sha256: Sha256
    preprocessing_sha256: Sha256


class FactorRunBinding(Contract):
    schema_version: Literal["mineagent.factor-run-binding.v1"] = (
        "mineagent.factor-run-binding.v1"
    )
    bundle: FactorBundle
    dataset: DatasetBinding
    snapshots: tuple[SnapshotBinding, ...] = Field(min_length=1, max_length=64)
    exposure_history: ExposureHistory = Field(default_factory=ExposureHistory)

    @model_validator(mode="after")
    def exact(self):
        members = {v.factor_version_id: v for v in self.bundle.members}
        bindings = {v.version.factor_version_id: v.version for v in self.snapshots}
        if len(bindings) != len(self.snapshots) or bindings != members:
            raise ValueError("Run must bind exactly every frozen bundle member")
        validate_history_inheritance(
            self.bundle.exposure_history, self.exposure_history
        )
        return self

    @property
    def sha256(self):
        return digest(self)


class AnalysisProtocol(Contract):
    schema_version: Literal["mineagent.factor-analysis-protocol.v1"] = (
        "mineagent.factor-analysis-protocol.v1"
    )
    kind: Literal[
        "direct_factor_analysis",
        "native_prediction_analysis",
        "native_portfolio_analysis",
    ]
    snapshot_id: UUID
    snapshot_output_sha256: Sha256
    window: DateWindow
    label_sha256: Sha256
    label_horizon_sessions: Annotated[int, Field(strict=True, ge=1, le=4096)]
    calculation_universe_sha256: Sha256
    missing_policy: Literal["pairwise_valid"] = "pairwise_valid"
    estimator_version: Identifier
    return_aggregation: Literal["not_applicable", "arithmetic_sum", "compound"] = (
        "not_applicable"
    )

    @model_validator(mode="after")
    def meaning(self):
        portfolio = self.kind == "native_portfolio_analysis"
        if portfolio == (self.return_aggregation == "not_applicable"):
            raise ValueError("Portfolio aggregation semantics must be explicit")
        return self


class AnalysisResult(Contract):
    schema_version: Literal["mineagent.factor-analysis.v1"] = (
        "mineagent.factor-analysis.v1"
    )
    protocol: AnalysisProtocol
    status: Literal["not_evaluated", "succeeded", "failed", "cancelled"]
    ic: Annotated[float, Field(ge=-1, le=1)] | None = None
    rank_ic: Annotated[float, Field(ge=-1, le=1)] | None = None
    cumulative_return: float | None = None
    observations: Count = 0
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def typed(self):
        if self.status != "succeeded" and any(
            x is not None for x in (self.ic, self.rank_ic, self.cumulative_return)
        ):
            raise ValueError("Unsuccessful analysis cannot publish metrics")
        if self.protocol.kind == "native_portfolio_analysis" and (
            self.ic is not None or self.rank_ic is not None
        ):
            raise ValueError("Portfolio performance is not factor/prediction IC")
        if (
            self.protocol.kind != "native_portfolio_analysis"
            and self.cumulative_return is not None
        ):
            raise ValueError("IC analysis cannot impersonate portfolio returns")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("Failed analysis needs a categorical error")
        return self


class StageValue(Contract):
    listing_id: Identifier
    value: float

    @field_validator("value", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Stage numeric values cannot be booleans or strings")  # noqa: TRY004
        return value


class StageCall(Contract):
    schema_version: Literal["mineagent.observable-stage.v1"] = (
        "mineagent.observable-stage.v1"
    )
    request_id: UUID
    decision_id: Identifier
    event_seq: Count
    stage: Literal["eligibility", "score", "allocation"]
    input_digest: Sha256
    predecessor_output_digest: Sha256 | None = None
    listing_ids: tuple[Identifier, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.listing_ids)) != len(self.listing_ids):
            raise ValueError("Stage listings must be unique")
        return self


class StageReply(Contract):
    schema_version: Literal["mineagent.observable-stage-reply.v1"] = (
        "mineagent.observable-stage-reply.v1"
    )
    request_id: UUID
    decision_id: Identifier
    event_seq: Count
    stage: Literal["eligibility", "score", "allocation"]
    input_digest: Sha256
    eligible_listing_ids: tuple[Identifier, ...] | None = Field(
        default=None, max_length=100
    )
    values: tuple[StageValue, ...] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def typed(self):
        if self.stage == "eligibility":
            if self.eligible_listing_ids is None or self.values is not None:
                raise ValueError("Eligibility returns only a mask")
            ids = self.eligible_listing_ids
        else:
            if self.values is None or self.eligible_listing_ids is not None:
                raise ValueError("Score/allocation returns only numeric values")
            ids = tuple(v.listing_id for v in self.values)
            if self.stage == "allocation" and (
                any(v.value < 0 or v.value > 1 for v in self.values)
                or sum(v.value for v in self.values) > 1 + 1e-12
            ):
                raise ValueError("Initial profile is bounded long-only allocation")
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate stage listing")
        return self


def validate_stage_reply(call: StageCall, reply: StageReply) -> None:
    for name in ("request_id", "decision_id", "event_seq", "stage", "input_digest"):
        if getattr(call, name) != getattr(reply, name):
            raise ValueError("Stage reply does not match its exact host request")
    ids = (
        reply.eligible_listing_ids
        if reply.stage == "eligibility"
        else tuple(v.listing_id for v in reply.values or ())
    )
    if not set(ids or ()) <= set(call.listing_ids):
        raise ValueError("Stage reply includes a listing outside its host input")
    if reply.stage in {"score", "allocation"} and set(ids or ()) != set(
        call.listing_ids
    ):
        raise ValueError(
            "Score/allocation must explicitly cover each host-eligible listing"
        )
