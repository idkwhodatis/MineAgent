from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.openevolve_adapter.contracts import (
    DevelopmentWindow,
    ObjectiveProfile,
    SearchOperationContext,
    SeedPreparation,
    SourceBinding,
)
from packages.openevolve_adapter.objective import (
    Observation,
    population_metrics,
    score,
    verify_population_score,
)
from packages.openevolve_adapter.source import (
    canonical_source,
    prepare_marker_region,
    region,
    validate_descendant,
    verify_controller_source,
)


def profile(**kwargs):
    return ObjectiveProfile(
        windows=(
            DevelopmentWindow(
                id="dev",
                start=date(2020, 1, 1),
                end=date(2020, 2, 1),
                weight_millionths=1_000_000,
            ),
        ),
        **kwargs,
    )


def observation(value, drawdown=0, trades=0):
    return Observation("dev", uuid4(), "a" * 64, value, drawdown, trades)


@pytest.mark.parametrize("value", [-5.0, -2.0, 0.0, 3.0])
def test_negative_and_zero_are_real_scores_without_implicit_trade_gate(value):
    result = score(profile(), (observation(value),))
    assert result.eligible and result.combined_score == value
    assert population_metrics(result) == {"combined_score": value}
    assert not verify_population_score({"error": 0.0}, result)


@pytest.mark.parametrize("value", [None, True, False, float("nan"), float("inf"), "1"])
def test_invalid_value_never_becomes_zero_or_population(value):
    result = score(profile(), (observation(value),))
    assert not result.eligible and result.combined_score is None
    with pytest.raises(ValueError):
        population_metrics(result)


def test_only_explicit_trade_constraint_rejects_no_trade_observation():
    assert score(profile(), (observation(0),)).eligible
    assert (
        score(profile(minimum_closed_trades=1), (observation(0),)).reason
        == "explicit_trade_constraint"
    )


def test_score_uses_full_precision_and_rejects_boolean_spoof():
    result = score(profile(), (observation(1.23456789, 0.12345678),))
    assert result.combined_score == pytest.approx(1.11111111)
    assert not verify_population_score({"combined_score": True}, result)
    assert not verify_population_score({"combined_score": 1.11}, result)


def test_exact_window_order_and_evaluation_identity():
    windows = tuple(
        DevelopmentWindow(
            id=name,
            start=date(2020, 1, 1),
            end=date(2020, 2, 1),
            weight_millionths=weight,
        )
        for name, weight in [("one", 250000), ("two", 750000)]
    )
    p = ObjectiveProfile(windows=windows)
    a = Observation("one", uuid4(), "a" * 64, -5, 0, 0)
    b = Observation("two", uuid4(), "b" * 64, -1, 0, 0)
    assert score(p, (a, b)).combined_score == -2
    with pytest.raises(ValueError):
        score(p, (b, a))
    with pytest.raises(ValueError):
        score(p, (a,))


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "minimize"},
        {"metric_paths": ("metrics.max_drawdown", "metrics.annualized_return")},
        {"unknown": 1},
    ],
)
def test_objective_definition_is_closed(change):
    data = profile().model_dump()
    with pytest.raises(ValidationError):
        ObjectiveProfile(**(data | change))


@pytest.mark.parametrize(
    "source",
    [
        b"\xef\xbb\xbfx=1\n",
        b"x=1\r\n",
        b"x=1\r",
        b"x=\0\n",
        b"\xff",
        b"# coding: latin-1\nx=1\n",
    ],
)
def test_source_encoding_requires_explicit_transform(source):
    with pytest.raises(ValueError):
        canonical_source(source)


def test_marker_transform_preserves_code_but_does_not_assert_parity():
    original = b"import math\n\ndef calculate(x):\n    return x + 1\n"
    marked = prepare_marker_region(original, first_line=3, last_line=4)
    assert original == marked.replace(b"# EVOLVE-BLOCK-START\n", b"").replace(
        b"# EVOLVE-BLOCK-END\n", b""
    )
    valid = marked.replace(b"return x + 1", b"return x + 2")
    assert (
        validate_descendant(valid, marked).source_sha256 != region(marked).source_sha256
    )
    with pytest.raises(ValueError):
        validate_descendant(valid.replace(b"import math", b"import os"), marked)


def test_markers_inside_strings_are_not_capability():
    with pytest.raises(ValueError):
        region(b's = """\n# EVOLVE-BLOCK-START\nx=1\n# EVOLVE-BLOCK-END\n"""\n')


def test_actual_controller_source_compared_not_file_twice():
    admitted = b"x=1\n"
    verify_controller_source("x=1\n", admitted)
    with pytest.raises(ValueError):
        verify_controller_source("x=1", admitted)


def test_preparation_cannot_mutate_existing_version_identity():
    original = SourceBinding(
        strategy_version_id=uuid4(), source_sha256="a" * 64, manifest_sha256="b" * 64
    )
    assert SeedPreparation(kind="none", original=original, prepared=original)
    with pytest.raises(ValidationError):
        SeedPreparation(
            kind="marker_parity_required",
            original=original,
            prepared=original,
            transform_sha256="c" * 64,
            parity_profile="native_marker_parity.v1",
        )


def test_typed_search_context_is_not_admitted_as_neutral_fixture():
    from packages.research_runtime.broker_contracts import FixtureContext

    context = SearchOperationContext(
        owner_id=uuid4(),
        run_id=uuid4(),
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256="c" * 64,
    )
    with pytest.raises(ValidationError):
        FixtureContext.model_validate(context.model_dump())


def complete_spec():
    from packages.openevolve_adapter.contracts import (
        InputFile,
        NoFactors,
        SearchSpec,
        StrategyExecutionPlan,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    source = SourceBinding(
        strategy_version_id=uuid4(), source_sha256="a" * 64, manifest_sha256="b" * 64
    )
    objective = profile()
    plan = StrategyExecutionPlan(
        dataset_version_id=uuid4(),
        dataset_sha256="c" * 64,
        files=(InputFile(logical_name="prices", sha256="d" * 64, size_bytes=100),),
        universe_sha256="e" * 64,
        listing_map_sha256="f" * 64,
        calendar_sha256="a" * 64,
        price_semantics_sha256="b" * 64,
        engine_config_sha256="c" * 64,
        parameter_sha256="d" * 64,
        state_schema_sha256="e" * 64,
        capture_profile_sha256="f" * 64,
        evaluator_image="sha256:" + "a" * 64,
        market="US",
        warmup_sessions=0,
        rng_seed=42,
        windows=objective.windows,
    )
    return SearchSpec(
        owner_id=uuid4(),
        run_id=uuid4(),
        preparation=SeedPreparation(kind="none", original=source, prepared=source),
        execution=plan,
        objective=objective,
        factors=NoFactors(),
        runtime_image="sha256:" + "b" * 64,
        runtime_lock_sha256="c" * 64,
        adapter_patch_sha256="d" * 64,
        prompt_profile_sha256="e" * 64,
        budget=BudgetVector(
            calls=2,
            tokens=4096,
            mutation_slots=2,
            evaluator_executions=3,
            wall_milliseconds=600000,
            storage_bytes=64 * 1024 * 1024,
            process_slots=2,
        ),
        maximum_mutation_slots=2,
        population_size=8,
        archive_size=8,
    )


def test_complete_search_spec_json_roundtrip_and_digest():
    from packages.openevolve_adapter.contracts import SearchSpec

    spec = complete_spec()
    assert SearchSpec.model_validate_json(spec.model_dump_json()) == spec
    assert SearchSpec.model_validate_json(spec.model_dump_json()).sha256 == spec.sha256


@pytest.mark.parametrize(
    "key",
    [
        "calls",
        "tokens",
        "mutation_slots",
        "evaluator_executions",
        "wall_milliseconds",
        "storage_bytes",
        "process_slots",
    ],
)
def test_search_rejects_unexecutable_budget(key):
    from packages.openevolve_adapter.contracts import SearchSpec

    data = complete_spec().model_dump()
    data["budget"][key] = 0
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("maximum_mutation_slots", True),
        ("upstream_sha", "f" * 40),
        ("unknown", 1),
        ("population_size", "8"),
    ],
)
def test_search_spec_rejects_coercion_future_fields_and_wrong_upstream(key, value):
    from packages.openevolve_adapter.contracts import SearchSpec

    data = complete_spec().model_dump()
    data[key] = value
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize(
    "change", ["duplicate_files", "different_window", "bool_warmup", "unknown_plan"]
)
def test_execution_plan_rejects_incomplete_or_ambiguous_identity(change):
    from packages.openevolve_adapter.contracts import SearchSpec

    data = complete_spec().model_dump()
    if change == "duplicate_files":
        data["execution"]["files"] *= 2
    elif change == "different_window":
        data["execution"]["windows"][0]["id"] = "other"
    elif change == "bool_warmup":
        data["execution"]["warmup_sessions"] = True
    else:
        data["execution"]["legacy_config"] = {}
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)
