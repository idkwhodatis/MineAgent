"""Hand-checkable fixed factor computations; no arbitrary code or model execution.

These functions really compute the displayed samples. Synthetic daily calendars
are explicit fixtures, not exchange calendars or verified point-in-time markets.
"""

from __future__ import annotations

import hashlib
import inspect
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import pstdev
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .contracts import (
    CalculationUniverse,
    Contract,
    DatasetBinding,
    DateWindow,
    ExposureHistory,
    FactorDefinition,
    FactorVersionRef,
    FeatureRow,
    FeatureSnapshotManifest,
    Identifier,
    LocalVerification,
    Parameter,
    SnapshotInput,
    aware_utc,
    digest,
    row_key,
    rows_digest,
    source_sha256,
    validate_rows,
)

FixedKind = Literal["momentum", "risk_adjusted_momentum", "cross_sectional_rank"]
_NS = UUID("d73ae519-c45d-59d6-aaf2-de9a2947e1d4")


def momentum(prices: tuple[float, ...]) -> float | None:
    if len(prices) < 2 or prices[0] == 0:
        return None
    return prices[-1] / prices[0] - 1


def risk_adjusted_momentum(prices: tuple[float, ...]) -> float | None:
    if len(prices) < 3 or any(price == 0 for price in prices[:-1]):
        return None
    returns = tuple(prices[i] / prices[i - 1] - 1 for i in range(1, len(prices)))
    volatility = pstdev(returns)
    if volatility <= 1e-15:
        return None
    return (prices[-1] / prices[0] - 1) / volatility


def cross_sectional_rank(
    values: tuple[tuple[str, float], ...],
) -> tuple[tuple[str, float], ...]:
    """Ascending average rank / valid count; highest value ranks 1, ties average."""
    count = len(values)
    return (
        tuple(
            (
                listing,
                (
                    sum(other < value for _, other in values)
                    + (sum(other == value for _, other in values) + 1) / 2
                )
                / count,
            )
            for listing, value in values
        )
        if count
        else ()
    )


_FUNCTIONS = {
    "momentum": momentum,
    "risk_adjusted_momentum": risk_adjusted_momentum,
    "cross_sectional_rank": cross_sectional_rank,
}


def fixed_factor_code(kind: FixedKind) -> str:
    if kind not in _FUNCTIONS:
        raise ValueError("Unsupported fixed factor")
    # Rank is explicitly rank(risk-adjusted momentum), not rank of arbitrary code.
    source = inspect.getsource(_FUNCTIONS[kind])
    if kind == "cross_sectional_rank":
        source = inspect.getsource(risk_adjusted_momentum) + "\n" + source
    return "from statistics import pstdev\n\n" + source


def fixture_definition(
    kind: FixedKind, *, lookback_sessions: int = 2, lag_sessions: int = 0
) -> FactorDefinition:
    from .contracts import InputField

    if type(lookback_sessions) is not int or not 1 <= lookback_sessions <= 120:
        raise ValueError("Fixed factor lookback must be 1..120 sessions")
    if kind != "momentum" and lookback_sessions < 2:
        raise ValueError("Volatility/rank requires at least two returns")
    return FactorDefinition(
        code_sha256=source_sha256(fixed_factor_code(kind)),
        factor_kind="cross_sectional"
        if kind == "cross_sectional_rank"
        else "time_series",
        input_fields=(
            InputField(name="close", unit="currency", price_semantics="unadjusted"),
        ),
        lookback_sessions=lookback_sessions,
        lag_sessions=lag_sessions,
        parameters=(Parameter(name="fixed_operator", value=kind),),
        output_unit={
            "momentum": "fractional_return",
            "risk_adjusted_momentum": "return_per_daily_volatility",
            "cross_sectional_rank": "cross_sectional_percentile",
        }[kind],
    )


class CalendarSession(Contract):
    session_date: date
    open_time: datetime
    close_time: datetime

    _times = field_validator("open_time", "close_time")(aware_utc)

    @model_validator(mode="after")
    def ordered(self):
        if self.open_time >= self.close_time:
            raise ValueError("Session open must precede close")
        return self


class MarketCalendar(Contract):
    schema_version: Literal["mineagent.factor-calendar.v1"] = (
        "mineagent.factor-calendar.v1"
    )
    timezone: Identifier
    sessions: tuple[CalendarSession, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def ordered(self):
        tz = ZoneInfo(self.timezone)
        dates = [s.session_date for s in self.sessions]
        if dates != sorted(set(dates)):
            raise ValueError("Calendar sessions must be sorted and unique")
        for session in self.sessions:
            if (
                session.open_time.astimezone(tz).date() != session.session_date
                or session.close_time.astimezone(tz).date() != session.session_date
            ):
                raise ValueError("Calendar session dates differ from timezone")
        if any(
            a.close_time >= b.open_time
            for a, b in zip(self.sessions, self.sessions[1:])
        ):
            raise ValueError("Calendar sessions overlap")
        return self

    @property
    def sha256(self):
        return digest(self)


class MarketObservation(Contract):
    listing_id: Identifier
    session_date: date
    close: float | None
    available_at: datetime

    _time = field_validator("available_at")(aware_utc)

    @field_validator("close", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or (
            value is not None and not isinstance(value, (int, float))
        ):
            raise ValueError("Close must be numeric or explicitly missing")
        if value is not None and value < 0:
            raise ValueError("Fixed fixture does not support negative prices")
        return value


class CalculationSample(Contract):
    factor_version_id: UUID
    listing_id: Identifier
    session_date: date
    operator: FixedKind
    inputs: tuple[MarketObservation, ...]
    peer_scores: tuple[tuple[Identifier, float], ...] = ()
    output: FeatureRow
    explanation_source: Literal["fixed_fixture_recomputed"] = "fixed_fixture_recomputed"


@dataclass(frozen=True)
class FixedMaterialization:
    manifest: FeatureSnapshotManifest
    rows: tuple[FeatureRow, ...]
    samples: tuple[CalculationSample, ...]
    verification: LocalVerification


def materialize_fixed_factor(
    snapshot_input: SnapshotInput,
    observations: tuple[MarketObservation, ...],
    calendar: MarketCalendar,
    *,
    snapshot_id: UUID | None = None,
) -> FixedMaterialization:
    definition = snapshot_input.definition
    params = {p.name: p.value for p in definition.parameters}
    kind = params.get("fixed_operator")
    expected = fixture_definition(
        kind,
        lookback_sessions=definition.lookback_sessions,
        lag_sessions=definition.lag_sessions,
    )
    if definition != expected:
        raise ValueError("Fixed materializer rejects nonfixture source/definition")
    if (
        calendar.sha256 != snapshot_input.calendar_sha256
        or calendar.timezone != snapshot_input.market_timezone
    ):
        raise ValueError("Calendar differs from frozen snapshot input")
    if (
        digest([o.model_dump(mode="json") for o in observations])
        != snapshot_input.dataset.input_files_sha256
    ):
        raise ValueError("Observation inventory differs from frozen dataset input")
    if snapshot_input.dataset.source_quality != "synthetic_fixture":
        raise ValueError(
            "Fixed fixture publication requires explicitly synthetic input"
        )
    listings = snapshot_input.calculation_universe.listing_ids
    lookup = {(o.session_date, o.listing_id): o for o in observations}
    if len(lookup) != len(observations):
        raise ValueError("Duplicate input observation")
    session_dates = {s.session_date for s in calendar.sessions}
    if any(
        o.listing_id not in listings or o.session_date not in session_dates
        for o in observations
    ):
        raise ValueError("Input observation lies outside calculation universe/calendar")
    for observation in observations:
        session = next(
            s for s in calendar.sessions if s.session_date == observation.session_date
        )
        if observation.available_at < session.close_time:
            raise ValueError("Close input cannot precede the actual synthetic close")
    rows, samples = [], []
    lookback, lag = definition.lookback_sessions, definition.lag_sessions
    for index, session in enumerate(calendar.sessions):
        if (
            not snapshot_input.window.start
            <= session.session_date
            < snapshot_input.window.end
        ):
            continue
        if index + lag >= len(calendar.sessions):
            raise ValueError("Calendar does not cover declared availability lag")
        window_sessions = calendar.sessions[max(0, index - lookback) : index + 1]
        info = {}
        for listing in listings:
            inputs = tuple(
                lookup[(s.session_date, listing)]
                for s in window_sessions
                if (s.session_date, listing) in lookup
            )
            value, state, reason = None, "valid", None
            if index < lookback:
                state, reason = "warmup_insufficient", "requires_complete_lookback"
            elif len(inputs) != len(window_sessions) or any(
                o.close is None for o in inputs
            ):
                state, reason = "input_missing", "required_close_missing"
            else:
                prices = tuple(o.close for o in inputs)
                value = (
                    momentum(prices)
                    if kind == "momentum"
                    else risk_adjusted_momentum(prices)
                )
                if value is None:
                    state, reason = (
                        "zero_denominator",
                        "zero_start_price_or_daily_volatility",
                    )
                elif not math.isfinite(value):
                    value, state, reason = (
                        None,
                        "calculation_error",
                        "nonfinite_fixed_result",
                    )
            ready = max([session.close_time, *(o.available_at for o in inputs)])
            info[listing] = (inputs, value, state, reason, ready)
        peers = tuple(
            (listing, info[listing][1])
            for listing in listings
            if info[listing][1] is not None
        )
        ranked = (
            dict(cross_sectional_rank(peers)) if kind == "cross_sectional_rank" else {}
        )
        # A cross-sectional rank requires the entire declared calculation cut,
        # including readiness of missing peers, not only the traded subset.
        collective_ready = max(value[4] for value in info.values())
        for listing in listings:
            inputs, value, state, reason, ready = info[listing]
            if kind == "cross_sectional_rank":
                ready = collective_ready
                if value is not None:
                    value = ranked[listing]
            membership = snapshot_input.calculation_universe.membership_available_at
            lag_ready = calendar.sessions[index + lag].close_time
            available = max(ready, membership, lag_ready)
            evidence = {
                "inputs": [o.model_dump(mode="json") for o in inputs],
                "operator": kind,
                "peers": peers if kind == "cross_sectional_rank" else (),
                "calendar": calendar.sha256,
                "definition": definition.sha256,
            }
            row = FeatureRow(
                event_time=session.close_time,
                session_date=session.session_date,
                listing_id=listing,
                factor_version_id=snapshot_input.version.factor_version_id,
                available_at=available,
                value=value,
                value_state=state,
                missing_reason=reason,
                input_window_start=window_sessions[0].session_date,
                input_window_end=session.session_date + timedelta(days=1),
                input_evidence_digest=digest(evidence),
                required_input_available_at=ready,
                membership_available_at=membership,
                lag_available_at=lag_ready,
            )
            rows.append(row)
            samples.append(
                CalculationSample(
                    factor_version_id=row.factor_version_id,
                    listing_id=listing,
                    session_date=row.session_date,
                    operator=kind,
                    inputs=inputs,
                    peer_scores=peers if kind == "cross_sectional_rank" else (),
                    output=row,
                )
            )
    ordered = tuple(sorted(rows, key=row_key))
    validate_rows(snapshot_input, ordered)
    valid = sum(row.value_state == "valid" for row in ordered)
    manifest = FeatureSnapshotManifest(
        snapshot_id=snapshot_id or uuid4(),
        input=snapshot_input,
        input_digest=snapshot_input.sha256,
        output_digest=rows_digest(ordered),
        row_count=len(ordered),
        valid_count=valid,
        missing_count=len(ordered) - valid,
    )
    verification = LocalVerification(
        kind="fixed_fixture_recomputed",
        admission_profile="fixed-factors.v1",
        evidence_sha256=digest(
            {
                "input": manifest.input_digest,
                "output": manifest.output_digest,
                "samples": [s.model_dump(mode="json") for s in samples],
            }
        ),
    )
    return FixedMaterialization(manifest, ordered, tuple(samples), verification)


def demo_inputs():
    """Eight explicitly synthetic weekdays spanning the March 2024 US DST change."""
    tz = ZoneInfo("America/New_York")
    days = (
        date(2024, 3, 6),
        date(2024, 3, 7),
        date(2024, 3, 8),
        date(2024, 3, 11),
        date(2024, 3, 12),
        date(2024, 3, 13),
        date(2024, 3, 14),
        date(2024, 3, 15),
    )
    calendar = MarketCalendar(
        timezone="America/New_York",
        sessions=tuple(
            CalendarSession(
                session_date=day,
                open_time=datetime.combine(day, time(9, 30), tz),
                close_time=datetime.combine(day, time(16), tz),
            )
            for day in days
        ),
    )
    prices = {
        "fixture:XNAS:AAA": (100.0, 110.0, 121.0, 120.0, 126.0, 130.0, 128.0, 132.0),
        "fixture:XNAS:BBB": (100.0, 100.0, 120.0, 118.0, 125.0, 120.0, 122.0, 126.0),
        "fixture:XNAS:CCC": (100.0, 90.0, 99.0, 102.0, 101.0, 104.0, 106.0, 108.0),
    }
    observations = tuple(
        MarketObservation(
            listing_id=listing,
            session_date=session.session_date,
            close=prices[listing][i],
            available_at=session.close_time,
        )
        for i, session in enumerate(calendar.sessions)
        for listing in sorted(prices)
    )
    return calendar, observations


def demo_fixture(
    kind: FixedKind = "momentum",
    *,
    factor_version_id: UUID,
    dataset_version_id: UUID,
    snapshot_id: UUID | None = None,
    lookback_sessions: int = 2,
    lag_sessions: int = 0,
    dataset_content_sha256: str | None = None,
) -> FixedMaterialization:
    calendar, observations = demo_inputs()
    if type(lag_sessions) is not int or not 0 <= lag_sessions < len(calendar.sessions):
        raise ValueError("Demo fixture lag must fit its frozen calendar")
    definition = fixture_definition(
        kind, lookback_sessions=lookback_sessions, lag_sessions=lag_sessions
    )
    inputs_hash = digest([o.model_dump(mode="json") for o in observations])
    listings = tuple(sorted({o.listing_id for o in observations}))
    universe = CalculationUniverse(
        content_sha256=digest(listings),
        listing_ids=listings,
        membership_sha256=digest({"members": listings, "kind": "synthetic_static"}),
        membership_available_at=calendar.sessions[0].open_time,
        membership_history="synthetic_fixture",
    )
    snapshot_input = SnapshotInput(
        version=FactorVersionRef(
            factor_version_id=factor_version_id,
            definition_sha256=definition.sha256,
            code_sha256=definition.code_sha256,
        ),
        definition=definition,
        dataset=DatasetBinding(
            dataset_version_id=dataset_version_id,
            content_sha256=dataset_content_sha256
            or digest(
                {
                    "schema_version": "mineagent.factor-fixture-dataset.v1",
                    "files": {"observations.json": inputs_hash},
                    "is_synthetic": True,
                    "evaluation_compatibility": False,
                }
            ),
            input_files_sha256=inputs_hash,
            price_semantics="unadjusted",
            availability_revision_sha256=digest(
                [o.available_at.isoformat() for o in observations]
            ),
            source_quality="synthetic_fixture",
        ),
        calculation_universe=universe,
        window=DateWindow(
            start=calendar.sessions[0].session_date,
            end=calendar.sessions[-1 - lag_sessions].session_date + timedelta(days=1),
        ),
        market_timezone=calendar.timezone,
        calendar_sha256=calendar.sha256,
        environment_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        adapter_version="fixed-factors.v1",
        materialization_profile="synthetic_hand_checkable.v1",
        exposure_history=ExposureHistory(completeness="local_observed"),
    )
    return materialize_fixed_factor(
        snapshot_input, observations, calendar, snapshot_id=snapshot_id
    )
