"""Validate the fixed container proof schema, not user research results."""

import json
import math
import re
import sys
from pathlib import Path


def validate(proof):
    if proof.get("schema") != "mineagent.qlib-installed-pair-proof.v1":
        raise ValueError("Proof schema")
    for key in (
        "isolated_python",
        "module_paths_installed_only",
        "actual_upstream_runner",
    ):
        if proof.get(key) is not True:
            raise ValueError(f"Proof missing {key}")
    if type(proof.get("uid")) is not int or proof["uid"] == 0:
        raise ValueError("Nonroot proof required")
    if (
        proof.get("scope") != "synthetic_installed_qlib_and_rd_runner_only"
        or proof.get("production_admission") is not False
        or proof.get("hosted_acceptance") is not False
    ):
        raise ValueError("Proof scope drift")
    if proof.get("rd_feedback_scope") != "validation_only":
        raise ValueError("RD feedback scope")
    if (
        type(proof.get("socket_connect_attempts")) is not int
        or proof["socket_connect_attempts"] != 0
    ):
        raise ValueError("Network attempts")
    direct = proof["direct"]
    if (
        direct["schema"] != "mineagent.qlib-direct-proof.v1"
        or direct["socket_connect_attempts"] != 0
        or direct["qlib_commit"] != "54355232463878d2eebb91fe0ee5fa7fa1f5976c"
    ):
        raise ValueError("Direct proof drift")
    if direct["result"]["defined_days"] != 2 or direct["result"]["total_pairs"] != 9:
        raise ValueError("Direct fixture drift")
    if (
        direct["result"]["kind"] != "direct_factor_analysis"
        or direct["groups"]["kind"] != "factor_label_group_analysis"
    ):
        raise ValueError("Direct namespaces")
    for key in ("baseline", "combined"):
        native = proof[key]
        if (
            native["schema"] != "mineagent.qlib-native-fixture-proof.v1"
            or native["qlib_version"] != "0.9.8"
        ):
            raise ValueError("Native schema/version")
        if (
            native["prediction_rows"] != 60
            or native["portfolio_days"] != 14
            or native["late_training_labels_excluded"] != 1
            or native["socket_connect_attempts"] != 0
        ):
            raise ValueError("Native fixture closure")
        for field in (
            "model_text_sha256",
            "model_config_sha256",
            "prediction_sha256",
            "portfolio_sha256",
            "actual_learner_input_sha256",
            "fit_selection_sha256",
        ):
            if not re.fullmatch("[a-f0-9]{64}", native[field]):
                raise ValueError("Native hash")
        for field in (
            "ic",
            "rank_ic",
            "development_validation_ic",
            "development_validation_rank_ic",
            "turnover_arithmetic_sum",
            "net_return_compound",
        ):
            if type(native[field]) not in (int, float) or not math.isfinite(
                native[field]
            ):
                raise ValueError("Native finite metric")
        if (
            native["turnover_arithmetic_sum"] <= 0
            or native["aggregation"] != "compound_daily_return_minus_daily_cost"
        ):
            raise ValueError("Native portfolio semantics")
    if proof["baseline"]["combined_input_sha256"] is not None or proof["baseline"][
        "learner_features"
    ] != ["frozen_sin"]:
        raise ValueError("Baseline was not actually separate")
    if not re.fullmatch(
        "[a-f0-9]{64}", proof["combined"]["combined_input_sha256"]
    ) or proof["combined"]["learner_features"] != ["frozen_sin", "generated_cos"]:
        raise ValueError("Combined input was not retained")
    if proof["baseline"]["prediction_sha256"] == proof["combined"]["prediction_sha256"]:
        raise ValueError("Prediction did not change")
    return True


if __name__ == "__main__":
    path = Path(sys.argv[1])
    if not 0 < path.stat().st_size < 262144:
        raise SystemExit("Proof output budget")
    validate(json.loads(path.read_text()))
    print("Qlib installed-only proof output validated")
