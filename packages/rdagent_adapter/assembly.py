"""Pure host-side row assembly from already-decoded normalizer primitives.

Not an admission, stopped-process proof, hash-verified extraction, or publication
API. Those checks belong to the fenced host service BEFORE calling this helper.
No native HDF/Parquet parsing or candidate-selected availability is present.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field

from packages.factors.contracts import (
    Contract,
    FeatureRow,
    Sha256,
    SnapshotInput,
    digest,
    rows_digest,
    validate_rows,
)
from packages.rdagent_adapter.prefix import PrefixManifest


class CutoffRowAssembly(Contract):
    schema_version: Literal["mineagent.factor-row-assembly.v1"] = (
        "mineagent.factor-row-assembly.v1"
    )
    snapshot_input_sha256: Sha256
    projection_sha256: Sha256
    hdf_sha256: Sha256
    normalized_values_sha256: Sha256
    output_rows_sha256: Sha256
    row_count: int = Field(strict=True, ge=1, le=10_000)
    valid_count: int = Field(strict=True, ge=0, le=10_000)
    dependency_claim: Literal["provided_not_proven_read"] = "provided_not_proven_read"


def validate_projection_binding(snapshot: SnapshotInput, projection: PrefixManifest):
    if (
        projection.snapshot_input_sha256 != snapshot.sha256
        or projection.calendar_sha256 != snapshot.calendar_sha256
        or projection.market_timezone != snapshot.market_timezone
        or projection.membership_sha256
        != snapshot.calculation_universe.membership_sha256
        or projection.membership_available_at
        != snapshot.calculation_universe.membership_available_at
        or projection.preprocessing != snapshot.definition.preprocessing
        or projection.input_fields != snapshot.definition.input_fields
        or projection.lag_sessions != snapshot.definition.lag_sessions
        or tuple(item.listing_id for item in projection.listings)
        != tuple(sorted(snapshot.calculation_universe.listing_ids))
        or projection.lookback_prior_sessions != snapshot.definition.lookback_sessions
        or len(projection.provided_sessions) > snapshot.definition.lookback_sessions + 1
    ):
        raise ValueError("Projection differs from immutable input")


def assemble_cutoff_rows(
    snapshot: SnapshotInput,
    projection: PrefixManifest,
    normalized: tuple[tuple[int, int, float | None, int], ...],
    *,
    hdf_sha256: str,
    normalized_values_sha256: str,
):
    validate_projection_binding(snapshot, projection)
    if len(normalized) > projection.max_output_rows:
        raise ValueError("Normalizer output row cap")
    expected_ns = (
        (projection.output_event_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    observed = {}
    previous = -1
    for record in normalized:
        if type(record) is not tuple or len(record) != 4:
            raise ValueError("Invalid normalized row shape")
        event, code, value, state = record
        if (
            type(event) is not int
            or event != expected_ns
            or type(code) is not int
            or not 0 <= code < len(projection.listings)
            or code <= previous
            or type(state) is not int
            or state not in (0, 1)
            or (
                state == 0
                and (type(value) not in (float, int) or not math.isfinite(value))
            )
            or (state == 1 and value is not None)
        ):
            raise ValueError("Invalid normalized primitive row")
        observed[code] = (value, state)
        previous = code
    rows = []
    for listing in projection.listings:
        value, state = observed.get(listing.code, (None, 1))
        reason = (
            None
            if state == 0
            else (
                "candidate_reported_missing"
                if listing.code in observed
                else "candidate_omitted_output"
            )
        )
        rows.append(
            FeatureRow(
                event_time=projection.output_event_time,
                session_date=projection.output_session,
                listing_id=listing.listing_id,
                factor_version_id=snapshot.version.factor_version_id,
                available_at=projection.publication_ready_at,
                value=value,
                value_state="valid" if state == 0 else "not_available",
                missing_reason=reason,
                input_window_start=projection.provided_window.start,
                input_window_end=projection.provided_window.end,
                input_evidence_digest=digest(projection),
                required_input_available_at=projection.required_input_available_at,
                membership_available_at=projection.membership_available_at,
                lag_available_at=projection.lag_available_at,
            )
        )
    rows = tuple(rows)
    validate_rows(snapshot, rows)
    receipt = CutoffRowAssembly(
        snapshot_input_sha256=snapshot.sha256,
        projection_sha256=digest(projection),
        hdf_sha256=hdf_sha256,
        normalized_values_sha256=normalized_values_sha256,
        output_rows_sha256=rows_digest(rows),
        row_count=len(rows),
        valid_count=sum(row.value_state == "valid" for row in rows),
    )
    return rows, receipt
