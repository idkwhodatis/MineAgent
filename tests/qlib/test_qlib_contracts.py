"""API-environment contract tests; no optional Qlib import."""

from datetime import date

import pytest
from pydantic import ValidationError

from packages.qlib_adapter.contracts import DirectDay, DirectResult, FactorPoint


def test_values_are_finite_numeric_not_string_or_boolean():
    for value in ("1", True, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            FactorPoint(session=date(2020, 1, 1), listing_id="A", value=value)


def test_unknown_fields_and_direct_portfolio_namespace_rejected():
    with pytest.raises(ValidationError):
        FactorPoint(session=date(2020, 1, 1), listing_id="A", value=1, score=1)
    day = DirectDay(
        session=date(2020, 1, 1), pair_count=3, ic=0.5, rank_ic=0.5, reason="defined"
    )
    fields = {
        "protocol_sha256": "a" * 64,
        "days": (day,),
        "ic": 0.5,
        "rank_ic": 0.5,
        "defined_days": 1,
        "total_pairs": 3,
    }
    DirectResult(**fields)
    for extra in (
        {"cumulative_return": 0.1},
        {"kind": "native_prediction_analysis"},
        {"total_pairs": 4},
        {"ic": 0.4},
    ):
        with pytest.raises(ValidationError):
            DirectResult(**(fields | extra))


def test_undefined_correlation_is_missing_not_zero():
    for reason in ("constant_cross_section", "insufficient_pairs"):
        with pytest.raises(ValidationError):
            DirectDay(
                session=date(2020, 1, 1), pair_count=1, ic=0, rank_ic=0, reason=reason
            )


def test_portfolio_compounds_actual_daily_net_returns_and_closes_turnover():
    from packages.qlib_adapter.contracts import NativeDay, NativePortfolioResult

    days = (
        NativeDay(
            session=date(2020, 1, 1),
            gross_return=0.1,
            cost=0.01,
            turnover=0.5,
            account_value=109,
        ),
        NativeDay(
            session=date(2020, 1, 2),
            gross_return=-0.1,
            cost=0.01,
            turnover=0.2,
            account_value=97.01,
        ),
    )
    fields = {
        "native_binding_sha256": "a" * 64,
        "daily_report_sha256": "b" * 64,
        "days": days,
        "net_return_compound": 1.09 * 0.89 - 1,
        "turnover_arithmetic_sum": 0.7,
    }
    NativePortfolioResult(**fields)
    for mutation in (
        {"net_return_compound": -0.02},
        {"turnover_arithmetic_sum": 0.6},
        {"kind": "direct_factor_analysis"},
        {"aggregation": "arithmetic_sum"},
    ):
        with pytest.raises(ValidationError):
            NativePortfolioResult(**(fields | mutation))


def test_native_artifacts_reject_pickle_media_and_open_roles():
    from packages.qlib_adapter.contracts import NativeArtifact

    for role, media in [
        ("model_text", "application/octet-stream"),
        ("prediction", "text/plain"),
        ("pickle", "application/json"),
    ]:
        with pytest.raises(ValidationError):
            NativeArtifact(role=role, media_type=media, sha256="a" * 64, size_bytes=10)


def test_native_binding_forbids_overlapping_training_and_test():
    from uuid import UUID

    from packages.qlib_adapter.contracts import NativeBinding

    data = {
        name: UUID(int=i + 1)
        for i, name in enumerate(("owner_id", "dataset_id", "run_id", "attempt_id"))
    }
    data.update(
        {
            name: "a" * 64
            for name in (
                "dataset_sha256",
                "label_manifest_sha256",
                "combined_input_sha256",
                "actual_learner_input_sha256",
                "model_config_sha256",
                "model_text_sha256",
                "prediction_sha256",
                "baseline_receipt_sha256",
                "daily_report_sha256",
                "fit_selection_sha256",
            )
        }
    )
    data.update(
        training_fit_cutoff="2020-01-31T21:00:00Z",
        validation_selection_cutoff="2020-02-29T21:00:00Z",
        generation=1,
        factor_snapshot_ids=(UUID(int=10),),
        training_window={"start": "2020-01-01", "end": "2020-02-01"},
        validation_window={"start": "2020-02-01", "end": "2020-03-01"},
        test_window={"start": "2020-03-01", "end": "2020-04-01"},
    )
    NativeBinding(**data)
    data["test_window"] = {"start": "2020-01-15", "end": "2020-04-01"}
    with pytest.raises(ValidationError):
        NativeBinding(**data)
