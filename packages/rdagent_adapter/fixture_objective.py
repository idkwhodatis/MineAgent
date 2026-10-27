"""One maintained three-listing momentum oracle; not alpha/general-source validity."""

import math
from datetime import date

from packages.factors.contracts import FeatureRow, SnapshotInput, validate_rows
from packages.factors.fixtures import demo_fixture

EXPECTED = {
    "fixture:XNAS:AAA": 132.0 / 130.0 - 1.0,
    "fixture:XNAS:BBB": 126.0 / 120.0 - 1.0,
    "fixture:XNAS:CCC": 108.0 / 104.0 - 1.0,
}


def measured_fixture_objective(snapshot_input, rows):
    snapshot_input = SnapshotInput.model_validate(snapshot_input)
    rows = tuple(FeatureRow.model_validate(row) for row in rows)
    maintained = demo_fixture(
        factor_version_id=snapshot_input.version.factor_version_id,
        dataset_version_id=snapshot_input.dataset.dataset_version_id,
    ).manifest.input
    if (
        snapshot_input.dataset != maintained.dataset
        or snapshot_input.calculation_universe != maintained.calculation_universe
        or snapshot_input.calendar_sha256 != maintained.calendar_sha256
        or snapshot_input.window.start != date(2024, 3, 15)
        or snapshot_input.window.end != date(2024, 3, 16)
        or snapshot_input.definition.lookback_sessions != 2
        or snapshot_input.definition.lag_sessions != 0
        or snapshot_input.definition.preprocessing.kind != "none"
    ):
        raise ValueError(
            "Objective supports only complete maintained single-day L2 fixture"
        )
    validate_rows(snapshot_input, rows)
    if len(rows) != 3 or {r.listing_id for r in rows} != set(EXPECTED):
        return False
    return all(
        r.value_state == "valid"
        and r.value is not None
        and math.isclose(r.value, EXPECTED[r.listing_id], rel_tol=0, abs_tol=1e-12)
        and r.session_date == date(2024, 3, 15)
        for r in rows
    )
