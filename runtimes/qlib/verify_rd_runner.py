"""Local integration proof for real QlibFactorRunner's combined-factor branch.

Not a production supervisor or caller-configurable process launcher. Uses the
pinned audited source tree until the separate reduced RD/Qlib wheel is sealed.
"""

import json
import socket
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QLIB_PYTHON = REPO.parent / "mineagent-qlib-runtime/bin/python"


def verify(root):
    from runtimes.rdagent.bootstrap import configure_upstream

    configure_upstream(root)
    import numpy as np
    import pandas as pd
    from rdagent.components.coder.CoSTEER.evaluators import (
        CoSTEERMultiFeedback,
        CoSTEERSingleFeedback,
    )
    from rdagent.components.coder.factor_coder.factor import (
        FactorFBWorkspace,
        FactorTask,
    )
    from rdagent.core.scenario import Scenario
    from rdagent.scenarios.qlib.developer.factor_runner import QlibFactorRunner
    from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorExperiment
    from rdagent.scenarios.qlib.experiment.workspace import QlibFBWorkspace

    class FrozenScenario(Scenario):
        def __init__(self):
            pass

        @property
        def background(self):
            return "Offline synthetic frozen fixture; no provider or discovery."

        @property
        def rich_style_description(self):
            return self.background

        def get_runtime_environment(self):
            return "Fixed offline synthetic fixture; no caller configuration."

        def get_source_data_desc(self, task=None):
            return self.background

        def get_scenario_all_desc(self, **kwargs):
            return self.background

    class NormalizedFactor(FactorFBWorkspace):
        def execute(self, data_type="All"):
            if data_type != "All":
                raise ValueError("Unexpected factor mode")
            days = pd.bdate_range("2020-01-01", periods=65)[:-1]
            rows = [
                (d, s, np.cos(i / (3 + j) + j))
                for i, d in enumerate(days)
                for j, s in enumerate(("AA", "BB", "CC", "DD"))
            ]
            return "Trusted synthetic normalized factor fixture.", pd.DataFrame(
                rows, columns=["datetime", "instrument", "generated_cos"]
            ).set_index(["datetime", "instrument"]).sort_index()

    class FixedQlibWorkspace(QlibFBWorkspace):
        def __init__(self, path, expected_config):
            # Public workspace execution seam. No upstream template injection,
            # generated shell, read_pickle, Docker or environment construction.
            self.workspace_path = path
            path.mkdir()
            self.expected_config = expected_config
            self.receipt = None

        def execute(self, qlib_config_name, run_env):
            if qlib_config_name != self.expected_config:
                raise ValueError("Unexpected native configuration")
            # run_env is upstream descriptive defaults, NOT executable authority.
            # Proof always runs the immutable synthetic fixture configuration.
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.workspace_path),
                "MLFLOW_DISABLE_TELEMETRY": "true",
                "DO_NOT_TRACK": "true",
                "OMP_NUM_THREADS": "1",
            }
            subprocess.run(
                [
                    str(QLIB_PYTHON),
                    "-I",
                    "-m",
                    "runtimes.qlib.verify_native",
                    "--runner-fixture",
                ],
                cwd=self.workspace_path,
                env=env,
                capture_output=True,
                timeout=60,
                check=True,
            )
            self.receipt = json.loads(
                (self.workspace_path / "native_result.json").read_text()
            )
            return pd.Series(
                {
                    "IC": self.receipt["development_validation_ic"],
                    "Rank IC": self.receipt["development_validation_rank_ic"],
                }
            ), "Actual fixed Qlib fixture completed."

    runner = QlibFactorRunner(FrozenScenario())
    baseline = QlibFactorExperiment(sub_tasks=[])
    baseline.experiment_workspace = FixedQlibWorkspace(
        root / "baseline", "conf_baseline.yaml"
    )
    runner.develop(baseline)
    assert baseline.result is not None
    task = FactorTask(
        factor_name="generated_cos",
        factor_description="Frozen synthetic column",
        factor_formulation="cos(t/(3+j)+j)",
    )
    experiment = QlibFactorExperiment(sub_tasks=[task], based_experiments=[baseline])
    experiment.sub_workspace_list = [NormalizedFactor(target_task=task)]
    experiment.prop_dev_feedback = CoSTEERMultiFeedback(
        [
            CoSTEERSingleFeedback(
                execution="fixture",
                return_checking="normalized",
                code="trusted fixture",
                final_decision=True,
            )
        ]
    )
    experiment.experiment_workspace = FixedQlibWorkspace(
        root / "combined", "conf_combined_factors.yaml"
    )
    assert not experiment.base_feature_codes
    runner.develop(experiment)
    actual = experiment.experiment_workspace.receipt
    assert experiment.result["IC"] == actual["development_validation_ic"]
    assert experiment.result["Rank IC"] == actual["development_validation_rank_ic"]
    assert actual["combined_input_sha256"] is not None
    assert actual["learner_features"] == ["frozen_sin", "generated_cos"]
    assert (
        actual["prediction_sha256"]
        != baseline.experiment_workspace.receipt["prediction_sha256"]
    )
    return {
        "schema": "mineagent.actual-qlib-factor-runner-proof.v1",
        "baseline": baseline.experiment_workspace.receipt,
        "combined": actual,
        "actual_upstream_runner": True,
        "based_experiments_count": 1,
        "base_feature_codes_used": False,
        "claim": "Local real runner integration; runtime isolation and dependency closure reported separately; not hosted acceptance",
    }


if __name__ == "__main__":
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("Offline RD runner proof")

    socket.socket.connect = socket.socket.connect_ex = deny
    with tempfile.TemporaryDirectory(prefix="mineagent-rd-qlib-") as temp:
        result = verify(Path(temp))
        assert not attempts
        result["socket_connect_attempts"] = len(attempts)
        print(json.dumps(result, sort_keys=True, indent=2))
