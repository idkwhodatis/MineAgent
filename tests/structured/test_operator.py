"""Closed-transform unit declarations, NOT real factor or AKQuant execution proof."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from packages.factors.contracts import FeatureRow, digest
from packages.strategy_sdk import (
    AccountView,
    BarView,
    DataRequirements,
    EngineProfile,
    ListingView,
    PriceAssumptions,
    StateCodec,
    StrategyManifest,
    UniverseAssumptions,
    build_observation,
    hash_json,
    parse_reply,
)
from packages.structured_factor_strategy.contracts import (
    STATE,
    ListingBinding,
    StructuredProgram,
)
from packages.structured_factor_strategy.operator import ClosedExchange, evaluate
from runtimes.portable_engine.runtime import reviewed_profile, target_orders


def fixture(values=(0.04, 0.02, -0.02)):
    day = date(2024, 3, 13)
    close = datetime(2024, 3, 13, 20, tzinfo=UTC)
    factor = uuid4()
    snapshot_id = uuid4()
    strategy = uuid4()
    program = StructuredProgram(
        signal_window={"start": day, "end": day + timedelta(days=1)},
        threshold=0.0,
        top_k=1,
        maximum_weight=0.5,
        maximum_gross_weight=1.0,
    )
    mappings = tuple(
        ListingBinding(
            factor_listing_id=f"factor:{i}", native_listing_id=UUID(int=i + 1)
        )
        for i in range(3)
    )
    rows = tuple(
        FeatureRow(
            event_time=close,
            session_date=day,
            listing_id=m.factor_listing_id,
            factor_version_id=factor,
            available_at=close,
            value=value,
            value_state="valid",
            input_window_start=day - timedelta(days=2),
            input_window_end=day + timedelta(days=1),
            input_evidence_digest="a" * 64,
            required_input_available_at=close,
            membership_available_at=close,
            lag_available_at=close,
        )
        for m, value in zip(mappings, values, strict=True)
    )
    listing = tuple(
        ListingView(
            listing_id=str(m.native_listing_id),
            instrument_id=str(m.native_listing_id),
            symbol=f"A{i}",
            mic="XNYS",
            currency="USD",
            calendar="XNYS",
            timezone="America/New_York",
            effective_from=date(2020, 1, 1),
            known_at=datetime(2020, 1, 1, tzinfo=UTC),
            lot_size=1,
        )
        for i, m in enumerate(mappings)
    )
    manifest = StrategyManifest(
        strategy_kind="portfolio",
        strategy_id=str(strategy),
        code_sha256=digest(program),
        parameters_sha256=digest(program),
        market="US",
        currency="USD",
        calendar="XNYS",
        timezone="America/New_York",
        data=DataRequirements(lookback_bars=1, warmup_bars=1, max_listings=3),
        universe=UniverseAssumptions(
            semantics="user_fixed",
            snapshot_sha256=hash_json([x.model_dump(mode="json") for x in listing]),
            survivorship_bias="possible",
        ),
        prices=PriceAssumptions(actions_snapshot_sha256=hash_json([])),
        state_schema=STATE,
        engine_profile=EngineProfile(
            profile_sha256=hash_json(reviewed_profile("US", 10000.0)),
            environment_sha256="b" * 64,
        ),
    )
    bars = tuple(
        BarView(
            listing_id=x.listing_id,
            session_date=day,
            completed_at=close,
            available_at=close,
            open=100.0,
            high=130.0,
            low=90.0,
            close=price,
            volume=1000.0,
        )
        for x, price in zip(listing, (104.0, 102.0, 98.0), strict=True)
    )
    observation = build_observation(
        manifest=manifest,
        decision_time=close,
        next_open_time=datetime(2024, 3, 14, 13, 30, tzinfo=UTC),
        session_date=day,
        listings=listing,
        history=bars,
        account=AccountView(
            as_of=close, currency="USD", cash=10000.0, equity=10000.0, positions=()
        ),
        state=StateCodec(STATE).encode({}),
    )
    inputs = NS(
        program=program,
        listing_bindings=mappings,
        rows=rows,
        execution_id=uuid4(),
        snapshot=NS(
            snapshot_id=snapshot_id, input=NS(version=NS(factor_version_id=factor))
        ),
    )
    return inputs, observation, manifest


def test_actual_closed_transform_targets_existing_sizing_without_claiming_fill():
    inputs, obs, manifest = fixture()
    record = evaluate(inputs, obs, 0)
    parse_reply(record.reply.model_dump_json().encode(), manifest, obs)
    assert [x.value for x in record.reads] == [0.04, 0.02, -0.02]
    assert record.eligible == (UUID(int=1), UUID(int=2))
    assert [(t.listing_id, t.value) for t in record.targets] == [
        (str(UUID(int=1)), 0.5)
    ]
    assert target_orders(
        record.reply.decision, obs, reviewed_profile("US", 10000.0)
    ) == [(str(UUID(int=1)), "buy", 48)]
    assert record.qualification == "closed_operator_transform_not_process_certificate"


def test_changed_declared_input_changes_explicit_transform_not_claimed_execution():
    inputs, obs, _ = fixture((-0.04, -0.02, 0.02))
    record = evaluate(inputs, obs, 0)
    assert [t.listing_id for t in record.targets] == [str(UUID(int=3))]
    assert target_orders(
        record.reply.decision, obs, reviewed_profile("US", 10000.0)
    ) == [(str(UUID(int=3)), "buy", 51)]


def test_future_row_not_observed_as_value_or_substituted_zero():
    inputs, obs, _ = fixture()
    inputs.rows = (
        inputs.rows[0].model_copy(
            update={"available_at": obs.decision_time + timedelta(seconds=1)}
        ),
        *inputs.rows[1:],
    )
    record = evaluate(inputs, obs, 0)
    assert record.reads[0].availability == "not_yet_available"
    assert record.reads[0].value is None
    assert record.reads[0].row_sha256 is None
    assert record.targets[0].listing_id == str(UUID(int=2))


def test_outside_declared_signal_window_holds_without_factor_read():
    inputs, obs, _ = fixture()
    inputs.program = inputs.program.model_copy(
        update={
            "signal_window": inputs.program.signal_window.model_copy(
                update={"start": date(2024, 3, 12), "end": date(2024, 3, 13)}
            )
        }
    )
    record = evaluate(inputs, obs, 0)
    assert record.reads == () and record.targets == ()
    assert record.reply.decision.action == "hold"


def test_duplicate_observation_cannot_create_second_stage_chain():
    inputs, obs, _ = fixture()
    exchange = ClosedExchange(inputs)
    exchange(obs)
    with pytest.raises(ValueError, match="strictly ordered"):
        exchange(obs)


@pytest.mark.parametrize(
    "change",
    [
        {"threshold": True},
        {"top_k": 0},
        {"maximum_weight": 1.1},
        {"score_operator": "user_python"},
        {"labels": []},
    ],
)
def test_closed_program_rejects_unbounded_or_custom_operation(change):
    inputs, _, _ = fixture()
    value = inputs.program.model_dump(mode="json")
    value.update(change)
    with pytest.raises(ValueError):
        StructuredProgram.model_validate(value)


def declared_input():
    """Synthetic grammar fixture only; cannot satisfy persisted issuer authority."""
    from packages.factors.contracts import SnapshotInput, rows_digest
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest
    from packages.factors.fixtures import demo_fixture
    from packages.structured_factor_strategy.contracts import (
        EngineProjection,
        StructuredEngineInput,
    )
    from tests.factors.test_execution_snapshot_contract import (
        manifest as declared_manifest,
    )

    inputs, obs, engine_manifest = fixture()
    base = demo_fixture(
        factor_version_id=inputs.snapshot.input.version.factor_version_id,
        dataset_version_id=uuid4(),
    ).manifest.input.model_dump(mode="json")
    base["window"] = inputs.program.signal_window.model_dump(mode="json")
    base["calculation_universe"]["listing_ids"] = [
        b.factor_listing_id for b in inputs.listing_bindings
    ]
    snapshot_input = SnapshotInput.model_validate(base)
    manifest = declared_manifest().model_dump(mode="json")
    manifest["input"] = snapshot_input.model_dump(mode="json")
    manifest["input_sha256"] = snapshot_input.sha256
    manifest["snapshot_id"] = str(inputs.snapshot.snapshot_id)
    manifest["row_count"] = 3
    manifest["valid_count"] = 3
    manifest["partitions"][0]["row_count"] = 3
    manifest["partitions"][0]["valid_count"] = 3
    manifest["output_rows_sha256"] = rows_digest(inputs.rows)
    snapshot = ExecutionSnapshotManifest.model_validate(manifest)
    engine = EngineProjection(
        manifest=engine_manifest,
        window={"start": obs.session_date, "end": obs.session_date + timedelta(days=2)},
        initial_cash=10000.0,
        initial_state=obs.state,
        input_identity={
            "run_config_sha256": digest(inputs.program),
            "market_data_sha256": "c" * 64,
        },
    )
    return StructuredEngineInput(
        owner_id=snapshot.owner_id,
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        execution_id=inputs.execution_id,
        structured_strategy_version_id=UUID(engine_manifest.strategy_id),
        program=inputs.program,
        snapshot=snapshot,
        rows=inputs.rows,
        listing_bindings=inputs.listing_bindings,
        market_sha256="c" * 64,
        engine=engine,
    )


def test_exact_input_wire_preserves_distinct_profile_and_rejects_unknown_labels():
    from packages.factors.contracts import canonical_bytes
    from packages.structured_factor_strategy.wire import read_input, write_input

    value = declared_input()
    raw = write_input(value)
    assert read_input(raw) == value
    changed = value.model_dump(mode="json")
    changed["labels"] = []
    with pytest.raises(ValueError):
        read_input(canonical_bytes(changed))
    with pytest.raises(ValueError):
        read_input(raw + b" ")


@pytest.mark.parametrize(
    "field", ["owner_id", "structured_strategy_version_id", "market_sha256"]
)
def test_input_identity_drift_rejected(field):
    from packages.factors.contracts import canonical_bytes
    from packages.structured_factor_strategy.wire import read_input

    value = declared_input().model_dump(mode="json")
    value[field] = "d" * 64 if field == "market_sha256" else str(uuid4())
    with pytest.raises(ValueError):
        read_input(canonical_bytes(value))
