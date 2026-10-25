"""Host-only frozen prefix projection, never an assertion of candidate reads/PIT.

Every exposed row is bound into the manifest. A delayed member stays in the
listing map even when it has no admissible observations. No future labels enter
this closed OHLCV projection.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import (
    Contract,
    DateWindow,
    InputField,
    Preprocessing,
    Sha256,
    SnapshotInput,
    aware_utc,
    canonical_bytes,
    digest,
)
from packages.rdagent_adapter.contracts import ImageDigest


class ListingCode(Contract):
    code: int = Field(strict=True, ge=0, le=2**32 - 1)
    listing_id: str = Field(min_length=1, max_length=200)


class MarketObservation(Contract):
    listing_id: str = Field(min_length=1, max_length=200)
    session: date
    event_time: datetime
    available_at: datetime
    values: tuple[float | None, ...] = Field(min_length=1, max_length=5)

    _times = field_validator("event_time", "available_at")(aware_utc)

    @field_validator("values", mode="before")
    @classmethod
    def numeric(cls, values):
        if any(
            v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)))
            for v in values
        ):
            raise ValueError("Only finite numeric market observations are accepted")
        return values

    @model_validator(mode="after")
    def ready(self):
        if self.available_at < self.event_time:
            raise ValueError("Observation availability precedes its event")
        return self


class ProjectionFile(Contract):
    name: Literal["observations.json", "listings.json"]
    sha256: Sha256
    size_bytes: int = Field(strict=True, ge=1, le=16_777_216)


class PrefixManifest(Contract):
    schema_version: Literal["mineagent.factor-prefix-input.v1"] = (
        "mineagent.factor-prefix-input.v1"
    )
    snapshot_input_sha256: Sha256
    host_cutoff: datetime
    output_event_time: datetime
    output_session: date
    market_timezone: str
    calendar_sha256: Sha256
    provided_window: DateWindow
    provided_sessions: tuple[date, ...] = Field(min_length=1, max_length=64)
    listings: tuple[ListingCode, ...] = Field(min_length=1, max_length=10_000)
    membership_sha256: Sha256
    membership_available_at: datetime
    input_fields: tuple[InputField, ...] = Field(min_length=1, max_length=5)
    preprocessing: Preprocessing
    lag_sessions: int = Field(strict=True, ge=0, le=4096)
    lag_available_at: datetime
    required_input_available_at: datetime
    observation_count: int = Field(strict=True, ge=0, le=640_000)
    input_files: tuple[ProjectionFile, ...] = Field(min_length=2, max_length=2)
    candidate_image: ImageDigest
    normalizer_image: ImageDigest
    candidate_profile: Literal["mineagent.factor-prefix-candidate.v1"] = (
        "mineagent.factor-prefix-candidate.v1"
    )
    normalizer_profile: Literal["mineagent.factor-numeric-hdf.v1"] = (
        "mineagent.factor-numeric-hdf.v1"
    )
    dependency_claim: Literal["provided_not_proven_read"] = "provided_not_proven_read"
    membership_policy: Literal["static_available_by_every_cutoff"] = (
        "static_available_by_every_cutoff"
    )
    max_output_rows: int = Field(strict=True, ge=1, le=10_000)
    max_output_bytes: int = Field(strict=True, ge=1024, le=16_777_216)

    _times = field_validator(
        "host_cutoff",
        "output_event_time",
        "membership_available_at",
        "lag_available_at",
        "required_input_available_at",
    )(aware_utc)

    @model_validator(mode="after")
    def closed(self):
        tz = ZoneInfo(self.market_timezone)
        if self.output_event_time.astimezone(tz).date() != self.output_session:
            raise ValueError("Output event and market session differ")
        if (
            self.output_event_time > self.host_cutoff
            or self.required_input_available_at > self.host_cutoff
        ):
            raise ValueError("Projection exposes an event/input not ready by cutoff")
        if self.lag_available_at < self.output_event_time:
            raise ValueError("Lag readiness cannot precede output observation")
        if self.membership_available_at > self.host_cutoff:
            raise ValueError("Static membership was not available at cutoff")
        if (
            self.preprocessing.kind == "fitted"
            and self.preprocessing.available_at > self.host_cutoff
        ):
            raise ValueError("Preprocessing was not fitted by cutoff")
        if tuple(sorted(set(self.provided_sessions))) != self.provided_sessions:
            raise ValueError("Provided sessions must be unique and sorted")
        if self.provided_sessions[-1] != self.output_session:
            raise ValueError("Prefix ends at its declared output session")
        if self.provided_window != DateWindow(
            start=self.provided_sessions[0], end=self.output_session + timedelta(days=1)
        ):
            raise ValueError("Provided window does not cover exact mounted sessions")
        if [x.code for x in self.listings] != list(range(len(self.listings))):
            raise ValueError("Listing codes must be consecutive and canonical")
        if [x.listing_id for x in self.listings] != sorted(
            {x.listing_id for x in self.listings}
        ):
            raise ValueError("Full listing map must be sorted and unique")
        if len(self.listings) > self.max_output_rows:
            raise ValueError("Output row cap omits calculation members")
        if [x.name for x in self.input_files] != ["listings.json", "observations.json"]:
            raise ValueError("Projection file inventory differs")
        fields = [x.name for x in self.input_fields]
        if len(set(fields)) != len(fields) or set(fields) - {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            raise ValueError("Only distinct OHLCV fields are mounted")
        return self

    @property
    def publication_ready_at(self):
        """Host readiness may be after computation cutoff for a lagged output."""
        fitted = (
            self.preprocessing.available_at
            if self.preprocessing.kind == "fitted"
            else self.output_event_time
        )
        return max(
            self.output_event_time,
            self.required_input_available_at,
            self.membership_available_at,
            self.lag_available_at,
            fitted,
        )

    @property
    def sha256(self):
        return digest(self)


def project_prefix(
    snapshot: SnapshotInput,
    *,
    host_cutoff: datetime,
    output_event_time: datetime,
    output_session: date,
    calendar_sessions: tuple[date, ...],
    observations: tuple[MarketObservation, ...],
    candidate_image: str,
    normalizer_image: str,
    lag_available_at: datetime,
    max_output_rows: int = 10_000,
    max_output_bytes: int = 16_777_216,
):
    """Return immutable manifest and exact two file payloads; perform no I/O.

    The caller supplies its already hash-verified frozen calendar and dataset;
    this pure function cannot grant source verification or process authority.
    """
    cutoff = aware_utc(host_cutoff)
    event = aware_utc(output_event_time)
    if (
        snapshot.definition.lookback_sessions < 1
        or snapshot.definition.lookback_sessions > 64
    ):
        raise ValueError("First prefix profile requires one to 64 mounted sessions")
    if (
        tuple(sorted(set(calendar_sessions))) != calendar_sessions
        or output_session not in calendar_sessions
    ):
        raise ValueError(
            "Frozen calendar is not sorted/unique or misses output session"
        )
    if not snapshot.window.start <= output_session < snapshot.window.end:
        raise ValueError("Output session is outside immutable development window")
    end = calendar_sessions.index(output_session) + 1
    sessions = calendar_sessions[
        max(0, end - snapshot.definition.lookback_sessions) : end
    ]
    mapping = tuple(
        ListingCode(code=i, listing_id=listing)
        for i, listing in enumerate(snapshot.calculation_universe.listing_ids)
    )
    codes = {x.listing_id: x.code for x in mapping}
    fields = snapshot.definition.input_fields
    tz = ZoneInfo(snapshot.market_timezone)
    seen = set()
    if len(observations) > 640_000:
        raise ValueError("Frozen observation input exceeds profile row limit")
    rows = []
    ready = event
    for row in observations:
        if row.listing_id not in codes:
            raise ValueError("Dataset observation substitutes an unknown listing")
        if (
            len(row.values) != len(fields)
            or row.event_time.astimezone(tz).date() != row.session
        ):
            raise ValueError("Observation does not match frozen fields/session")
        key = (row.session, row.listing_id)
        if key in seen:
            raise ValueError("Duplicate daily observation")
        seen.add(key)
        if (
            row.session not in sessions
            or row.event_time > cutoff
            or row.available_at > cutoff
        ):
            continue
        ready = max(ready, row.available_at)
        rows.append(
            {
                "listing_code": codes[row.listing_id],
                "session": row.session.isoformat(),
                "event_time": row.event_time.isoformat(),
                "available_at": row.available_at.isoformat(),
                "values": row.values,
            }
        )
    rows.sort(key=lambda x: (x["session"], x["listing_code"]))
    files = {
        "listings.json": canonical_bytes([x.model_dump(mode="json") for x in mapping]),
        "observations.json": canonical_bytes(rows),
    }
    # Canonical encoding of a tuple of models is explicit rather than an opaque pickle.
    import hashlib

    manifest = PrefixManifest(
        snapshot_input_sha256=snapshot.sha256,
        host_cutoff=cutoff,
        output_event_time=event,
        output_session=output_session,
        market_timezone=snapshot.market_timezone,
        calendar_sha256=snapshot.calendar_sha256,
        provided_window=DateWindow(
            start=sessions[0], end=output_session + timedelta(days=1)
        ),
        provided_sessions=sessions,
        listings=mapping,
        membership_sha256=snapshot.calculation_universe.membership_sha256,
        membership_available_at=snapshot.calculation_universe.membership_available_at,
        input_fields=fields,
        preprocessing=snapshot.definition.preprocessing,
        lag_sessions=snapshot.definition.lag_sessions,
        lag_available_at=lag_available_at,
        required_input_available_at=ready,
        observation_count=len(rows),
        input_files=tuple(
            ProjectionFile(
                name=name,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
            for name, payload in sorted(files.items())
        ),
        candidate_image=candidate_image,
        normalizer_image=normalizer_image,
        max_output_rows=max_output_rows,
        max_output_bytes=max_output_bytes,
    )
    return manifest, files
