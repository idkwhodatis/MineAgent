"""Pure synthetic binding/objective tests; no Qlib runtime or DB authority claim."""

from datetime import timedelta
from uuid import uuid4

import pytest

from packages.factors.contracts import DateWindow, digest, rows_digest
from packages.factors.fixtures import demo_fixture, demo_inputs
from packages.qlib_adapter.contracts import (
    DirectDay,
    DirectResult,
    LabelManifest,
    LabelRow,
)
from packages.qlib_adapter.execution_binding import bind_completed_snapshot
from packages.qlib_adapter.objective import (
    DirectObjectiveDeclaration,
    measure_direct_objective,
)


def inputs():
    value = demo_fixture(factor_version_id=uuid4(), dataset_version_id=uuid4())
    calendar, _ = demo_inputs()
    selected = calendar.sessions[3]
    inp = value.manifest.input.model_copy(
        update={
            "window": DateWindow(
                start=selected.session_date,
                end=selected.session_date + timedelta(days=1),
            )
        }
    )
    rows = tuple(r for r in value.rows if r.session_date == selected.session_date)
    end = calendar.sessions[4]
    labels = tuple(
        LabelRow(
            session=selected.session_date,
            listing_id=r.listing_id,
            horizon_end=end.session_date,
            horizon_end_time=end.close_time,
            available_at=end.close_time,
            value=(i + 1) * 0.01,
        )
        for i, r in enumerate(rows)
    )
    dates = tuple(s.session_date for s in calendar.sessions)
    manifest = LabelManifest(
        dataset_id=inp.dataset.dataset_version_id,
        dataset_sha256=inp.dataset.content_sha256,
        calendar_sha256=inp.calendar_sha256,
        calendar_sessions_sha256=digest([d.isoformat() for d in dates]),
        market_timezone=inp.market_timezone,
        universe_sha256=inp.calculation_universe.content_sha256,
        rows_sha256=digest([r.model_dump(mode="json") for r in labels]),
        row_count=len(labels),
        horizon_sessions=1,
        price_semantics=inp.dataset.price_semantics,
    )
    return {
        "snapshot_id": value.manifest.snapshot_id,
        "snapshot_input": inp,
        "output_sha256": rows_digest(rows),
        "rows": rows,
        "label_manifest": manifest,
        "labels": labels,
        "calendar_sessions": dates,
    }


def test_complete_snapshot_becomes_separate_label_bound_direct_protocol():
    args = inputs()
    result = bind_completed_snapshot(**args)
    assert result.protocol.kind == "direct_factor_analysis"
    assert result.protocol.snapshot_output_sha256 == args["output_sha256"]
    assert result.qualification == "input_integrity_not_analysis_execution"
    assert result.protocol.return_aggregation == "not_applicable"


@pytest.mark.parametrize(
    "field",
    [
        "dataset_sha256",
        "calendar_sha256",
        "universe_sha256",
        "calendar_sessions_sha256",
        "rows_sha256",
    ],
)
def test_label_source_substitution_fails(field):
    args = inputs()
    args["label_manifest"] = args["label_manifest"].model_copy(update={field: "f" * 64})
    with pytest.raises(ValueError):
        bind_completed_snapshot(**args)


@pytest.mark.parametrize(
    "change", ["partial_factor", "partial_label", "outcome", "horizon", "delay"]
)
def test_incomplete_or_temporally_unbound_analysis_fails(change):
    args = inputs()
    if change == "partial_factor":
        args["rows"] = args["rows"][:-1]
        args["output_sha256"] = rows_digest(args["rows"])
    elif change == "partial_label":
        args["labels"] = args["labels"][:-1]
        args["label_manifest"] = args["label_manifest"].model_copy(
            update={
                "row_count": len(args["labels"]),
                "rows_sha256": digest(
                    [r.model_dump(mode="json") for r in args["labels"]]
                ),
            }
        )
    elif change == "outcome":
        args["output_sha256"] = "f" * 64
    elif change == "horizon":
        args["label_manifest"] = args["label_manifest"].model_copy(
            update={"horizon_sessions": 2}
        )
    else:
        args["rows"] = tuple(
            r.model_copy(update={"available_at": r.event_time + timedelta(days=1)})
            for r in args["rows"]
        )
        args["output_sha256"] = rows_digest(args["rows"])
    with pytest.raises(ValueError):
        bind_completed_snapshot(**args)


def objective(projection, **changes):
    return DirectObjectiveDeclaration(
        window=projection.protocol.window,
        label_horizon_sessions=1,
        metric="rank_ic",
        direction="at_least",
        threshold=0.1,
        minimum_defined_days=1,
        minimum_pairs=2,
    ).model_copy(update=changes)


def measured(projection, *, value=0.5, reason="defined"):
    day = projection.factors[0].session
    pairs = sum(
        f.value is not None and l.value is not None
        for f, l in zip(projection.factors, projection.labels, strict=True)
    )
    return DirectResult(
        protocol_sha256=projection.protocol.sha256,
        days=(
            DirectDay(
                session=day, pair_count=pairs, ic=value, rank_ic=value, reason=reason
            ),
        ),
        ic=value,
        rank_ic=value,
        defined_days=0 if value is None else 1,
        total_pairs=pairs,
    )


@pytest.mark.parametrize("value,status", [(0.5, "passed"), (-0.5, "failed")])
def test_objective_uses_declared_metric_not_fixture_values(value, status):
    projection = bind_completed_snapshot(**inputs())
    result = measure_direct_objective(
        objective(projection), projection, measured(projection, value=value)
    )
    assert result.status == status and result.value == value
    assert result.qualification == "measurement_not_execution_or_adoption_authority"


def test_undefined_or_low_coverage_is_not_zero_or_failed():
    projection = bind_completed_snapshot(**inputs())
    args = inputs()
    args["rows"] = tuple(r.model_copy(update={"value": 1.0}) for r in args["rows"])
    args["output_sha256"] = rows_digest(args["rows"])
    constant_projection = bind_completed_snapshot(**args)
    result = measure_direct_objective(
        objective(constant_projection),
        constant_projection,
        measured(constant_projection, value=None, reason="constant_cross_section"),
    )
    assert result.status == "not_evaluated" and result.value is None
    low = measure_direct_objective(
        objective(projection, minimum_pairs=100), projection, measured(projection)
    )
    assert low.status == "not_evaluated" and low.reason == "insufficient_coverage"


def test_objective_cannot_swap_result_protocol_or_direction():
    projection = bind_completed_snapshot(**inputs())
    with pytest.raises(ValueError):
        measure_direct_objective(
            objective(projection),
            projection,
            measured(projection).model_copy(update={"protocol_sha256": "f" * 64}),
        )
    inverse = measure_direct_objective(
        objective(projection, direction="at_most", threshold=-0.1),
        projection,
        measured(projection, value=-0.5),
    )
    assert inverse.status == "passed"


def test_false_undefined_reason_cannot_hide_defined_inputs():
    projection = bind_completed_snapshot(**inputs())
    with pytest.raises(ValueError):
        measure_direct_objective(
            objective(projection),
            projection,
            measured(projection, value=None, reason="constant_cross_section"),
        )
