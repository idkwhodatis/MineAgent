"""Pure direct-analysis projection; integrity checks confer no runtime authority.

Callers must freshly resolve the completed execution snapshot and independent
retained label source. Labels never enter candidate computation inputs. This
module neither launches Qlib nor accepts a detached result as execution proof.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from packages.factors.contracts import (
    FeatureRow,
    SnapshotInput,
    digest,
    rows_digest,
    validate_rows,
)
from packages.qlib_adapter.contracts import (
    DirectProtocol,
    FactorPoint,
    LabelManifest,
    LabelRow,
)

MAX_ROWS = 10_000


@dataclass(frozen=True)
class DirectAnalysisProjection:
    protocol: DirectProtocol
    factors: tuple[FactorPoint, ...]
    labels: tuple[LabelRow, ...]
    calendar_sessions: tuple[date, ...]
    qualification: str = "input_integrity_not_analysis_execution"


def bind_completed_snapshot(
    *,
    snapshot_id,
    snapshot_input,
    output_sha256,
    rows,
    label_manifest,
    labels,
    calendar_sessions,
):
    """Validate complete exact window/universe and forward-only analysis labels."""
    if type(snapshot_id) is not UUID:
        raise ValueError("Exact snapshot identity required")
    inp = SnapshotInput.model_validate(snapshot_input)
    rows = tuple(FeatureRow.model_validate(row) for row in rows)
    manifest = LabelManifest.model_validate(label_manifest)
    labels = tuple(LabelRow.model_validate(row) for row in labels)
    calendar = tuple(calendar_sessions)
    if (
        not rows
        or len(rows) > MAX_ROWS
        or len(labels) > MAX_ROWS
        or not calendar
        or len(calendar) > 8192
        or any(type(day) is not date for day in calendar)
        or calendar != tuple(sorted(set(calendar)))
    ):
        raise ValueError("Bounded complete analysis inventory required")
    validate_rows(inp, rows)
    if rows_digest(rows) != output_sha256:
        raise ValueError("Execution output commitment differs")
    if (
        manifest.dataset_id,
        manifest.dataset_sha256,
        manifest.calendar_sha256,
        manifest.universe_sha256,
        manifest.market_timezone,
        manifest.price_semantics,
    ) != (
        inp.dataset.dataset_version_id,
        inp.dataset.content_sha256,
        inp.calendar_sha256,
        inp.calculation_universe.content_sha256,
        inp.market_timezone,
        inp.dataset.price_semantics,
    ):
        raise ValueError(
            "Labels substitute execution dataset/calendar/universe semantics"
        )
    if (
        manifest.calendar_sessions_sha256
        != digest([day.isoformat() for day in calendar])
        or manifest.row_count != len(labels)
        or manifest.rows_sha256
        != digest([row.model_dump(mode="json") for row in labels])
    ):
        raise ValueError("Label bytes or calendar differ from frozen declaration")
    days = tuple(day for day in calendar if inp.window.start <= day < inp.window.end)
    listings = tuple(sorted(inp.calculation_universe.listing_ids))
    if not days or len(days) * len(listings) > MAX_ROWS:
        raise ValueError("Analysis window exceeds complete row budget")
    keys = tuple((day, listing) for day in days for listing in listings)
    if tuple((row.session_date, row.listing_id) for row in rows) != keys:
        raise ValueError("Execution snapshot does not cover complete declared grid")
    if tuple((row.session, row.listing_id) for row in labels) != keys:
        raise ValueError(
            "Labels must cover the same complete grid with explicit missingness"
        )
    positions = {day: i for i, day in enumerate(calendar)}
    zone = ZoneInfo(inp.market_timezone)
    for factor, label in zip(rows, labels, strict=True):
        position = positions[label.session] + manifest.horizon_sessions
        if (
            position >= len(calendar)
            or label.horizon_end != calendar[position]
            or label.horizon_end_time.astimezone(zone).date() != label.horizon_end
        ):
            raise ValueError("Forward label horizon differs from frozen calendar")
        # v1 labels start at the feature session close. A delayed feature cannot
        # be silently scored against a return that began before it was available.
        if factor.value_state == "valid" and factor.available_at > factor.event_time:
            raise ValueError(
                "Delayed factor requires an explicit availability-aligned label protocol"
            )
    factors = tuple(
        FactorPoint(
            session=row.session_date,
            listing_id=row.listing_id,
            value=row.value if row.value_state == "valid" else None,
        )
        for row in rows
    )
    protocol = DirectProtocol(
        snapshot_id=snapshot_id,
        snapshot_output_sha256=output_sha256,
        factor_points_sha256=digest([row.model_dump(mode="json") for row in factors]),
        label_manifest=manifest,
        window=inp.window,
        listing_ids=listings,
    )
    return DirectAnalysisProjection(protocol, factors, labels, calendar)
