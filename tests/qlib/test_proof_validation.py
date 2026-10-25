"""Proof schema negatives; actual outputs are also checked by the dedicated job."""

from copy import deepcopy

import pytest

from runtimes.qlib.validate_proof import validate


def fixture():
    native = {
        "schema": "mineagent.qlib-native-fixture-proof.v1",
        "qlib_version": "0.9.8",
        "prediction_rows": 60,
        "portfolio_days": 14,
        "late_training_labels_excluded": 1,
        "socket_connect_attempts": 0,
        "ic": 0.1,
        "rank_ic": 0.1,
        "development_validation_ic": 0.1,
        "development_validation_rank_ic": 0.1,
        "turnover_arithmetic_sum": 0.5,
        "net_return_compound": 0.1,
        "aggregation": "compound_daily_return_minus_daily_cost",
    }
    native.update(
        {
            field: "a" * 64
            for field in (
                "model_text_sha256",
                "model_config_sha256",
                "prediction_sha256",
                "portfolio_sha256",
                "actual_learner_input_sha256",
                "fit_selection_sha256",
            )
        }
    )
    baseline = native | {
        "combined_input_sha256": None,
        "learner_features": ["frozen_sin"],
    }
    combined = native | {
        "combined_input_sha256": "b" * 64,
        "learner_features": ["frozen_sin", "generated_cos"],
        "prediction_sha256": "c" * 64,
    }
    return {
        "schema": "mineagent.qlib-installed-pair-proof.v1",
        "isolated_python": True,
        "module_paths_installed_only": True,
        "actual_upstream_runner": True,
        "uid": 65532,
        "scope": "synthetic_installed_qlib_and_rd_runner_only",
        "production_admission": False,
        "hosted_acceptance": False,
        "socket_connect_attempts": 0,
        "rd_feedback_scope": "validation_only",
        "baseline": baseline,
        "combined": combined,
        "direct": {
            "schema": "mineagent.qlib-direct-proof.v1",
            "socket_connect_attempts": 0,
            "qlib_commit": "54355232463878d2eebb91fe0ee5fa7fa1f5976c",
            "result": {
                "defined_days": 2,
                "total_pairs": 9,
                "kind": "direct_factor_analysis",
            },
            "groups": {"kind": "factor_label_group_analysis"},
        },
    }


def test_exact_proof_shape_and_negative_scope_guards():
    proof = fixture()
    assert validate(proof)
    for key, value in [
        ("uid", 0),
        ("production_admission", True),
        ("isolated_python", False),
        ("socket_connect_attempts", False),
        ("rd_feedback_scope", "test_holdout"),
    ]:
        with pytest.raises(ValueError):
            validate(proof | {key: value})


@pytest.mark.parametrize(
    "key,value",
    [
        ("prediction_rows", 59),
        ("portfolio_days", 13),
        ("late_training_labels_excluded", 0),
        ("learner_features", ["generated_cos"]),
        ("prediction_sha256", "a" * 64),
        ("turnover_arithmetic_sum", float("nan")),
    ],
)
def test_native_output_cannot_fake_fixture_closure(key, value):
    proof = deepcopy(fixture())
    proof["combined"][key] = value
    with pytest.raises(ValueError):
        validate(proof)
