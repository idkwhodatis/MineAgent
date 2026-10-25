"""Actual pinned Qlib native experiment on a generated, local-only fixture.

This is an integration proof, not a production entry point or result admission.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def run(root: Path, combined_path: Path | None = None):
    # Explicitly local tracking, without saving or loading Python object artifacts.
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
    os.environ["DO_NOT_TRACK"] = "true"
    os.environ["MLFLOW_TRACKING_URI"] = (root / "mlruns").as_uri()
    import numpy as np
    import pandas as pd
    import qlib
    from qlib.contrib.eva.alpha import calc_ic
    from qlib.contrib.evaluate import backtest_daily
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.workflow import R

    days = pd.bdate_range("2020-01-01", periods=65)
    instruments = ("AA", "BB", "CC", "DD")
    provider = root / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "calendars/day.txt").write_text(
        "".join(f"{d:%Y-%m-%d}\n" for d in days)
    )
    (provider / "instruments/all.txt").write_text(
        "".join(f"{s}\t{days[0]:%Y-%m-%d}\t{days[-1]:%Y-%m-%d}\n" for s in instruments)
    )
    records = []
    for j, instrument in enumerate(instruments):
        # Trusted synthetic fixture, not observed market data or investment evidence.
        t = np.arange(len(days), dtype=float)
        close = (30 + 7 * j) * np.exp(0.002 * t + 0.035 * np.sin(t / (3 + j) + j))
        folder = provider / "features" / instrument.lower()
        folder.mkdir(parents=True)
        for field, values in {
            "close": close,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": t * 0 + 1000000,
            "factor": t * 0 + 1,
            "change": np.r_[0, close[1:] / close[:-1] - 1],
        }.items():
            np.r_[0, values].astype("<f4").tofile(folder / f"{field}.day.bin")
        for i, day in enumerate(days[:-1]):
            records.append(
                (
                    day,
                    instrument,
                    np.sin(i / (3 + j) + j),
                    np.cos(i / (3 + j) + j),
                    close[i + 1] / close[i] - 1,
                )
            )
    frame = (
        pd.DataFrame(
            records,
            columns=[
                "datetime",
                "instrument",
                "frozen_sin",
                "frozen_cos",
                "forward_return",
            ],
        )
        .set_index(["datetime", "instrument"])
        .sort_index()
    )
    frame.columns = pd.MultiIndex.from_tuples(
        [
            ("feature", "frozen_sin"),
            ("feature", "frozen_cos"),
            ("label", "forward_return"),
        ]
    )
    # Baseline is genuine training on one frozen feature. The generated column
    # enters only through the exact parquet written by actual QlibFactorRunner.
    frame = frame.drop(columns=[("feature", "frozen_cos")])
    combined_sha256 = None
    if combined_path is not None:
        combined = pd.read_parquet(combined_path)
        if not combined.index.equals(frame.index) or list(combined.columns) != [
            ("feature", "generated_cos")
        ]:
            raise ValueError("Combined fixture schema or index drift")
        if not np.isfinite(combined.to_numpy()).all():
            raise ValueError("Non-finite combined fixture")
        combined_sha256 = hashlib.sha256(combined_path.read_bytes()).hexdigest()
        frame = pd.concat([frame, combined], axis=1)
    from datetime import UTC, datetime, time
    from uuid import UUID

    from packages.factors.contracts import canonical_bytes, digest
    from packages.qlib_adapter.contracts import LabelManifest, LabelRow, NativeBinding
    from packages.qlib_adapter.selection import select_fit_labels

    sessions = tuple(day.date() for day in days)
    positions = {day: index for index, day in enumerate(sessions)}
    label_rows = []
    for (day, listing), value in frame[("label", "forward_return")].items():
        horizon = sessions[positions[day.date()] + 1]
        event = datetime.combine(horizon, time(21), tzinfo=UTC)
        # Deliberate late-arrival training label: the proof must exclude it
        # despite its observation session being well inside the train window.
        availability = (
            datetime.combine(sessions[37], time(21), tzinfo=UTC)
            if day == days[0] and listing == "AA"
            else event
        )
        label_rows.append(
            LabelRow(
                session=day.date(),
                listing_id=listing,
                horizon_end=horizon,
                horizon_end_time=event,
                available_at=availability,
                value=float(value),
            )
        )
    label_rows = tuple(label_rows)
    label_manifest = LabelManifest(
        dataset_id=UUID(int=1),
        dataset_sha256=digest(
            [[str(r[0]), r[1], *[float(v) for v in r[2:]]] for r in records]
        ),
        calendar_sha256=digest([d.isoformat() for d in sessions]),
        calendar_sessions_sha256=digest([d.isoformat() for d in sessions]),
        market_timezone="UTC",
        universe_sha256=digest(instruments),
        rows_sha256=digest([r.model_dump(mode="json") for r in label_rows]),
        row_count=len(label_rows),
        horizon_sessions=1,
        price_semantics="unadjusted",
    )
    binding_fields = {
        key: "a" * 64
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
    binding = NativeBinding(
        owner_id=UUID(int=1),
        dataset_id=UUID(int=1),
        dataset_sha256=label_manifest.dataset_sha256,
        label_manifest_sha256=label_manifest.sha256,
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        factor_snapshot_ids=(UUID(int=4),),
        training_window={"start": sessions[0], "end": sessions[36]},
        validation_window={"start": sessions[37], "end": sessions[46]},
        test_window={"start": sessions[47], "end": sessions[62]},
        training_fit_cutoff=datetime.combine(sessions[36], time(21), tzinfo=UTC),
        validation_selection_cutoff=datetime.combine(
            sessions[46], time(21), tzinfo=UTC
        ),
        **binding_fields,
    )
    selection = select_fit_labels(
        binding, label_manifest, label_rows, calendar_sessions=sessions
    )
    late = [x for x in selection.excluded if x.reason == "not_available_at_fit_cutoff"]
    assert len(late) == 1 and late[0].row.session == sessions[0]
    accepted = {
        (pd.Timestamp(row.session), row.listing_id)
        for row in selection.training + selection.validation
    }
    accepted.update(key for key in frame.index if days[47] <= key[0] <= days[61])
    frame = frame.loc[sorted(accepted)]
    assert (days[0], "AA") not in frame.index
    (root / "fit_selection.json").write_bytes(canonical_bytes(selection))
    qlib.init(
        provider_uri=str(provider),
        region="us",
        kernels=1,
        expression_cache=None,
        dataset_cache=None,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": (root / "mlruns").as_uri(),
                "default_exp_name": "mineagent-offline-proof",
            },
        },
    )
    dataset = DatasetH(
        handler=DataHandlerLP.from_df(frame),
        segments={
            "train": (days[0], days[35]),
            "valid": (days[37], days[45]),
            "test": (days[47], days[61]),
        },
    )
    actual_features = pd.concat(
        [
            dataset.prepare(
                segment,
                col_set="feature",
                data_key=DataHandlerLP.DK_I
                if segment == "test"
                else DataHandlerLP.DK_L,
            )
            for segment in ("train", "valid", "test")
        ]
    ).sort_index()
    expected_features = frame.loc[actual_features.index, "feature"]
    pd.testing.assert_frame_equal(actual_features, expected_features)
    actual_features.to_parquet(root / "actual_learner_input.parquet")
    model = LGBModel(
        num_boost_round=16,
        early_stopping_rounds=None,
        num_threads=1,
        num_leaves=7,
        min_data_in_leaf=4,
        seed=0,
        deterministic=True,
        force_col_wise=True,
        learning_rate=0.1,
    )
    with R.start(experiment_name="mineagent-offline-proof"):
        model.fit(dataset, verbose_eval=0)
        prediction = model.predict(dataset)
        validation_prediction = model.predict(dataset, segment="valid")
    # LightGBM native text only: never persist or restore a Python model pickle.
    model.model.save_model(str(root / "model.txt"))
    configuration = {
        "model": "qlib.contrib.model.gbdt.LGBModel",
        "parameters": model.params,
        "num_boost_round": model.num_boost_round,
        "early_stopping_rounds": model.early_stopping_rounds,
        "strategy": "TopkDropoutStrategy",
        "topk": 2,
        "n_drop": 1,
        "train": [str(days[0]), str(days[35])],
        "valid": [str(days[37]), str(days[45])],
        "test": [str(days[47]), str(days[61])],
        "price": "close",
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "minimum_cost": 0,
        "trade_unit": 1,
        "initial_cash": 1000000,
        "provider_claim": "synthetic_fixture_not_real_market",
    }
    (root / "configuration.json").write_text(
        json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    )
    # The score is an actual fitted model prediction, never a renamed factor column.
    target = dataset.prepare("test", col_set="label").iloc[:, 0]
    ic, rank_ic = calc_ic(prediction, target)
    validation_target = dataset.prepare("valid", col_set="label").iloc[:, 0]
    validation_ic, validation_rank_ic = calc_ic(
        validation_prediction, validation_target
    )
    strategy = TopkDropoutStrategy(signal=prediction, topk=2, n_drop=1)
    report, _ = backtest_daily(
        start_time=days[48],
        end_time=days[61],
        strategy=strategy,
        account=1000000,
        benchmark=pd.Series(0.0, index=days),
        exchange_kwargs={
            "codes": list(instruments),
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 0,
            "trade_unit": 1,
        },
    )
    prediction.to_frame("score").to_parquet(root / "prediction.parquet")
    report.to_parquet(root / "portfolio.parquet")
    assert len(prediction) == 60 and len(report) == 14
    assert float(report["turnover"].sum()) > 0
    assert not prediction.equals(frame.loc[prediction.index, ("feature", "frozen_sin")])
    assert np.isfinite(report[["return", "cost", "turnover"]].to_numpy()).all()
    return {
        "schema": "mineagent.qlib-native-fixture-proof.v1",
        "qlib_version": qlib.__version__,
        "fit_selection_sha256": selection.sha256,
        "late_training_labels_excluded": len(late),
        "model_text_sha256": hashlib.sha256(
            (root / "model.txt").read_bytes()
        ).hexdigest(),
        "model_config_sha256": hashlib.sha256(
            (root / "configuration.json").read_bytes()
        ).hexdigest(),
        "prediction_rows": len(prediction),
        "combined_input_sha256": combined_sha256,
        "actual_learner_input_sha256": hashlib.sha256(
            (root / "actual_learner_input.parquet").read_bytes()
        ).hexdigest(),
        "learner_features": list(actual_features.columns),
        "learner_input_scope": "actual_train_valid_test_feature_matrices_labels_separately_bound",
        "benchmark": "zero_return_series",
        "aggregation": "compound_daily_return_minus_daily_cost",
        "portfolio_days": len(report),
        "prediction_sha256": hashlib.sha256(
            (root / "prediction.parquet").read_bytes()
        ).hexdigest(),
        "portfolio_sha256": hashlib.sha256(
            (root / "portfolio.parquet").read_bytes()
        ).hexdigest(),
        "development_validation_ic": float(validation_ic.mean()),
        "development_validation_rank_ic": float(validation_rank_ic.mean()),
        "ic": float(ic.mean()),
        "rank_ic": float(rank_ic.mean()),
        "turnover_arithmetic_sum": float(report["turnover"].sum()),
        "net_return_compound": float(
            (1 + report["return"] - report["cost"]).prod() - 1
        ),
        "claim": "Actual Qlib LGBModel + TopkDropout; synthetic local fixture only; not RD runner integration or hosted acceptance",
    }


if __name__ == "__main__":
    import socket
    import sys

    attempts = []

    def deny_connect(*args, **kwargs):
        import traceback

        attempts.append(traceback.format_stack(limit=12))
        raise RuntimeError("Native fixture is offline")

    socket.socket.connect = socket.socket.connect_ex = deny_connect
    if sys.argv[1:] == ["--runner-fixture"]:
        root = Path.cwd()
        combined = root / "combined_factors_df.parquet"
        result = run(root, combined if combined.exists() else None)
        result["socket_connect_attempts"] = len(attempts)
        assert not attempts, attempts
        (root / "native_result.json").write_text(json.dumps(result, sort_keys=True))
    elif not sys.argv[1:]:
        with tempfile.TemporaryDirectory(prefix="mineagent-qlib-native-") as tmp:
            result = run(Path(tmp))
            result["socket_connect_attempts"] = len(attempts)
            assert not attempts, attempts
            print(json.dumps(result, sort_keys=True, indent=2))
    else:
        raise SystemExit("This local proof accepts no runtime configuration")
