from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from packages.factors.contracts import digest
from packages.qlib_adapter.contracts import LabelManifest, LabelRow, NativeBinding
from packages.qlib_adapter.selection import select_fit_labels


def fixture():
    sessions = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(7))

    def row(day, available_day):
        return LabelRow(
            session=sessions[day],
            listing_id="A",
            horizon_end=sessions[day + 1],
            horizon_end_time=datetime(2020, 1, day + 2, 21, tzinfo=UTC),
            available_at=datetime(2020, 1, available_day, 21, tzinfo=UTC),
            value=0.1,
        )

    rows = (row(0, 2), row(1, 5), row(3, 5))
    labels = LabelManifest(
        dataset_id=UUID(int=1),
        dataset_sha256="a" * 64,
        calendar_sha256="b" * 64,
        calendar_sessions_sha256=digest([d.isoformat() for d in sessions]),
        market_timezone="UTC",
        universe_sha256="c" * 64,
        rows_sha256=digest([r.model_dump(mode="json") for r in rows]),
        row_count=3,
        horizon_sessions=1,
        price_semantics="unadjusted",
    )
    fields = {
        "owner_id": UUID(int=2),
        "dataset_id": UUID(int=1),
        "dataset_sha256": "a" * 64,
        "label_manifest_sha256": labels.sha256,
        "run_id": UUID(int=3),
        "attempt_id": UUID(int=4),
        "generation": 1,
        "factor_snapshot_ids": (UUID(int=5),),
        "training_window": {"start": sessions[0], "end": sessions[2]},
        "validation_window": {"start": sessions[3], "end": sessions[4]},
        "test_window": {"start": sessions[5], "end": sessions[6]},
        "training_fit_cutoff": datetime(2020, 1, 3, 22, tzinfo=UTC),
        "validation_selection_cutoff": datetime(2020, 1, 5, 22, tzinfo=UTC),
    }
    fields.update(
        {
            key: "d" * 64
            for key in (
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
    return NativeBinding(**fields), labels, rows, sessions


def test_late_available_training_label_is_excluded_despite_disjoint_windows():
    binding, labels, rows, sessions = fixture()
    selection = select_fit_labels(binding, labels, rows, calendar_sessions=sessions)
    assert selection.training == (rows[0],)
    assert selection.validation == (rows[2],)
    assert selection.excluded[0].row == rows[1]
    assert selection.excluded[0].reason == "not_available_at_fit_cutoff"


def test_fit_cutoff_cannot_enter_validation_window():
    binding, labels, rows, sessions = fixture()
    altered = binding.model_copy(
        update={"training_fit_cutoff": datetime(2020, 1, 4, tzinfo=UTC)}
    )
    with pytest.raises(ValueError, match="next evaluation window"):
        select_fit_labels(altered, labels, rows, calendar_sessions=sessions)


def test_fit_horizon_and_calendar_cannot_be_substituted():
    binding, labels, rows, sessions = fixture()
    with pytest.raises(ValueError, match="calendar"):
        select_fit_labels(binding, labels, rows, calendar_sessions=sessions[:-1])
    altered = labels.model_copy(update={"horizon_sessions": 2})
    binding = binding.model_copy(update={"label_manifest_sha256": altered.sha256})
    with pytest.raises(ValueError, match="horizon"):
        select_fit_labels(binding, altered, rows, calendar_sessions=sessions)


def test_native_result_closure_rejects_report_and_selection_substitution():
    from packages.qlib_adapter.contracts import (
        DirectDay,
        NativeArtifact,
        NativeDay,
        NativeEvidenceManifest,
        NativePortfolioResult,
        NativePredictionResult,
    )
    from packages.qlib_adapter.selection import validate_native_results

    binding, labels, rows, sessions = fixture()
    selection = select_fit_labels(binding, labels, rows, calendar_sessions=sessions)
    binding = binding.model_copy(update={"fit_selection_sha256": selection.sha256})
    fields = {
        "configuration": "model_config_sha256",
        "model_text": "model_text_sha256",
        "prediction": "prediction_sha256",
        "combined_input": "combined_input_sha256",
        "learner_input": "actual_learner_input_sha256",
        "daily_report": "daily_report_sha256",
        "baseline_receipt": "baseline_receipt_sha256",
        "fit_selection": "fit_selection_sha256",
    }
    artifacts = tuple(
        NativeArtifact(
            role=role,
            sha256=getattr(binding, key),
            size_bytes=1,
            media_type="text/plain"
            if role == "model_text"
            else "application/json"
            if role in ("configuration", "baseline_receipt", "fit_selection")
            else "application/vnd.apache.parquet",
        )
        for role, key in fields.items()
    )
    manifest = NativeEvidenceManifest(binding=binding, artifacts=artifacts)
    day = sessions[5]
    prediction = NativePredictionResult(
        native_binding_sha256=digest(binding),
        days=(
            DirectDay(
                session=day,
                pair_count=3,
                ic=None,
                rank_ic=None,
                reason="constant_cross_section",
            ),
        ),
        ic=None,
        rank_ic=None,
        defined_days=0,
        total_pairs=3,
    )
    portfolio = NativePortfolioResult(
        native_binding_sha256=digest(binding),
        daily_report_sha256=binding.daily_report_sha256,
        days=(
            NativeDay(
                session=day, gross_return=0.1, cost=0, turnover=0.5, account_value=110
            ),
        ),
        net_return_compound=0.1,
        turnover_arithmetic_sum=0.5,
    )
    assert validate_native_results(
        manifest, prediction, portfolio, selection, labels, calendar_sessions=sessions
    )
    with pytest.raises(ValueError, match="daily report"):
        validate_native_results(
            manifest,
            prediction,
            portfolio.model_copy(update={"daily_report_sha256": "e" * 64}),
            selection,
            labels,
            calendar_sessions=sessions,
        )
    with pytest.raises(ValueError, match="binding"):
        validate_native_results(
            manifest,
            prediction.model_copy(update={"native_binding_sha256": "e" * 64}),
            portfolio,
            selection,
            labels,
            calendar_sessions=sessions,
        )
    forged = selection.model_copy(
        update={"training": selection.training + (rows[1],), "excluded": ()}
    )
    with pytest.raises(ValueError, match="unavailable labels"):
        validate_native_results(
            manifest, prediction, portfolio, forged, labels, calendar_sessions=sessions
        )
    corrupted = tuple(
        a.model_copy(update={"sha256": "e" * 64}) if a.role == "daily_report" else a
        for a in artifacts
    )
    with pytest.raises(ValueError, match="hash binding"):
        NativeEvidenceManifest(binding=binding, artifacts=corrupted)
