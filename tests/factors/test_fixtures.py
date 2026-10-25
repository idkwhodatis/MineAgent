from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from packages.factors.contracts import SnapshotInput, digest, rows_digest
from packages.factors.fixtures import (
    cross_sectional_rank,
    demo_fixture,
    demo_inputs,
    fixed_factor_code,
    fixture_definition,
    materialize_fixed_factor,
    momentum,
    risk_adjusted_momentum,
)


def make(kind="momentum", **kwargs):
    return demo_fixture(
        kind,
        factor_version_id=UUID(int=1),
        dataset_version_id=UUID(int=2),
        snapshot_id=UUID(int=3),
        **kwargs,
    )


def test_actual_hand_checkable_momentum_values_and_samples():
    result = make()
    assert result.manifest.row_count == 24
    assert result.manifest.valid_count == 18
    rows = {r.listing_id: r for r in result.rows if r.session_date == date(2024, 3, 8)}
    assert rows["fixture:XNAS:AAA"].value == pytest.approx(0.21)
    assert rows["fixture:XNAS:BBB"].value == pytest.approx(0.2)
    assert rows["fixture:XNAS:CCC"].value == pytest.approx(-0.01)
    sample = next(s for s in result.samples if s.output == rows["fixture:XNAS:AAA"])
    assert [o.close for o in sample.inputs] == [100, 110, 121]
    assert sample.output.input_window_start == date(2024, 3, 6)
    assert sample.output.input_window_end == date(2024, 3, 9)
    assert sample.output.input_evidence_digest
    assert result.verification.source_pit_verified is False


def test_risk_adjusted_and_rank_are_real_distinct_computations():
    risk = make("risk_adjusted_momentum")
    rank = make("cross_sectional_rank")
    risk_rows = [r for r in risk.rows if r.session_date == date(2024, 3, 8)]
    rank_rows = [r for r in rank.rows if r.session_date == date(2024, 3, 8)]
    assert risk_rows[0].value is None
    assert risk_rows[0].value_state == "zero_denominator"
    assert risk_rows[1].value == pytest.approx(2)
    assert risk_rows[2].value == pytest.approx(-0.1)
    assert [r.value for r in rank_rows] == [None, 1, 0.5]
    assert rank.manifest.input.definition.output_unit == "cross_sectional_percentile"
    assert len(rank.samples[7].peer_scores) == 2


def test_rank_ties_average_and_zero_not_missing():
    assert cross_sectional_rank((("a", 0.0), ("b", 0.0))) == (("a", 0.75), ("b", 0.75))
    assert momentum((100.0, 100.0)) == 0
    assert risk_adjusted_momentum((100.0, 100.0, 100.0)) is None
    assert cross_sectional_rank(()) == ()


def test_repeat_materialization_same_inputs_same_ids_exact_bytes():
    first, second = make(), make()
    assert first == second
    assert first.manifest.output_digest == rows_digest(first.rows)
    assert first.manifest.input_digest == first.manifest.input.sha256
    assert (
        first.manifest.input.definition.code_sha256
        != make("risk_adjusted_momentum").manifest.input.definition.code_sha256
    )


def test_lookback_change_changes_identity_and_warmup():
    first, second = make(), make(lookback_sessions=3)
    assert first.manifest.input_digest != second.manifest.input_digest
    assert second.manifest.missing_count == 9
    assert len(second.samples[9].inputs) == 4


def test_lag_uses_calendar_not_24_hours_across_dst_weekend():
    result = make(lag_sessions=1)
    row = next(r for r in result.rows if r.session_date == date(2024, 3, 8))
    assert row.event_time == datetime(2024, 3, 8, 21, tzinfo=UTC)
    assert row.available_at == datetime(2024, 3, 11, 20, tzinfo=UTC)
    assert row.lag_available_at == row.available_at


def test_input_mutation_requires_new_frozen_digest():
    base = make()
    calendar, observations = demo_inputs()
    changed = (observations[0].model_copy(update={"close": 101.0}), *observations[1:])
    with pytest.raises(ValueError, match="inventory differs"):
        materialize_fixed_factor(base.manifest.input, changed, calendar)
    raw = base.manifest.input.model_dump(mode="json")
    raw["dataset"]["input_files_sha256"] = digest(
        [o.model_dump(mode="json") for o in changed]
    )
    raw["dataset"]["content_sha256"] = raw["dataset"]["input_files_sha256"]
    new = materialize_fixed_factor(SnapshotInput.model_validate(raw), changed, calendar)
    assert new.manifest.input_digest != base.manifest.input_digest
    assert new.manifest.output_digest != base.manifest.output_digest


def test_cross_section_waits_for_late_peer_not_only_traded_listing():
    base = make("cross_sectional_rank")
    calendar, observations = demo_inputs()
    changed = tuple(
        o.model_copy(update={"available_at": o.available_at + timedelta(days=2)})
        if o.listing_id.endswith("CCC") and o.session_date == date(2024, 3, 8)
        else o
        for o in observations
    )
    raw = base.manifest.input.model_dump(mode="json")
    raw["dataset"]["input_files_sha256"] = digest(
        [o.model_dump(mode="json") for o in changed]
    )
    new = materialize_fixed_factor(SnapshotInput.model_validate(raw), changed, calendar)
    rows = [r for r in new.rows if r.session_date == date(2024, 3, 8)]
    assert len({r.required_input_available_at for r in rows}) == 1
    assert rows[1].available_at == datetime(2024, 3, 10, 21, tzinfo=UTC)


def test_missing_inputs_not_zero_or_forward_filled():
    base = make()
    calendar, observations = demo_inputs()
    changed = tuple(
        o.model_copy(update={"close": None})
        if o.listing_id.endswith("AAA") and o.session_date == date(2024, 3, 8)
        else o
        for o in observations
    )
    raw = base.manifest.input.model_dump(mode="json")
    raw["dataset"]["input_files_sha256"] = digest(
        [o.model_dump(mode="json") for o in changed]
    )
    new = materialize_fixed_factor(SnapshotInput.model_validate(raw), changed, calendar)
    row = next(
        r
        for r in new.rows
        if r.listing_id.endswith("AAA") and r.session_date == date(2024, 3, 8)
    )
    assert row.value is None and row.value_state == "input_missing"


def test_arbitrary_definition_never_executes():
    base = make()
    calendar, observations = demo_inputs()
    raw = base.manifest.input.model_dump(mode="json")
    raw["definition"]["code_sha256"] = "a" * 64
    from packages.factors.contracts import FactorDefinition

    raw["version"]["code_sha256"] = "a" * 64
    raw["version"]["definition_sha256"] = FactorDefinition.model_validate(
        raw["definition"]
    ).sha256
    with pytest.raises(ValueError, match="nonfixture"):
        materialize_fixed_factor(
            SnapshotInput.model_validate(raw), observations, calendar
        )


@pytest.mark.parametrize(
    "kind", ["momentum", "risk_adjusted_momentum", "cross_sectional_rank"]
)
def test_definition_pins_exact_inspected_callable_source(kind):
    fixture_definition(kind).validate_source(fixed_factor_code(kind))
    with pytest.raises(ValueError):
        fixture_definition(kind).validate_source(fixed_factor_code(kind) + "\n")


def test_frozen_dataset_manifest_override_distinct_from_observation_digest():
    result = demo_fixture(
        factor_version_id=UUID(int=1),
        dataset_version_id=UUID(int=2),
        dataset_content_sha256="a" * 64,
    )
    assert result.manifest.input.dataset.content_sha256 == "a" * 64
    assert result.manifest.input.dataset.input_files_sha256 != "a" * 64


def test_calendar_lag_and_invalid_materialization_rejected():
    for lag in (True, -1, 8, 100):
        with pytest.raises(ValueError):
            make(lag_sessions=lag)
    with pytest.raises(ValueError):
        fixture_definition("risk_adjusted_momentum", lookback_sessions=1)
    with pytest.raises(ValueError):
        fixture_definition("unknown")
