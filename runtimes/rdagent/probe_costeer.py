"""Actual pinned CoSTEER control flow with explicit in-memory host stand-ins.

Not durable-broker, candidate execution, isolation, or runtime acceptance. This
probe cannot authorize execution and does not execute any returned source.
"""

from __future__ import annotations

import builtins
import inspect
import io
import json
import os
import pickle
import socket
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4


def main():
    original_cwd = Path.cwd()

    attempts = {"network": 0, "pickle": 0}

    def network_forbidden(*args, **kwargs):
        attempts["network"] += 1
        raise AssertionError("network attempt during offline probe")

    def pickle_forbidden(*args, **kwargs):
        attempts["pickle"] += 1
        raise AssertionError("pickle loading/file serialization attempt")

    socket.socket.connect = socket.socket.connect_ex = network_forbidden
    socket.create_connection = network_forbidden
    pickle.dump = pickle.load = pickle.loads = pickle_forbidden
    import dill

    dill.dump = dill.load = dill.loads = pickle_forbidden
    original_open = builtins.open
    original_io_open = io.open

    def guarded(path, *args, **kwargs):
        if str(path).endswith((".pkl", ".pickle")):
            pickle_forbidden()
        return original_open(path, *args, **kwargs)

    def guarded_io(path, *args, **kwargs):
        if str(path).endswith((".pkl", ".pickle")):
            pickle_forbidden()
        return original_io_open(path, *args, **kwargs)

    builtins.open = guarded
    io.open = guarded_io
    from runtimes.rdagent.bootstrap import configure_upstream

    # Reuse the published frozen domain fixture, not runtime APIs or credentials.
    from runtimes.rdagent.probe_fixture import research_dict

    with tempfile.TemporaryDirectory() as tmp:
        configure_upstream(Path(tmp))
        import pandas as pd
        from rdagent.components.coder.factor_coder.factor import FactorTask
        from rdagent.core.experiment import Experiment

        from packages.rdagent_adapter.broker_bridge import use_host_broker
        from packages.rdagent_adapter.contracts import FactorResearchConfig
        from packages.rdagent_adapter.upstream import MineAgentFactorCoSTEER
        from packages.research_runtime.control import BrokerAbort

        config = FactorResearchConfig.model_validate(research_dict())
        sources = [
            "# fixture valid but task-rejected\nvalue = 0.0\n",
            "# fixture execution failure stand-in\nraise RuntimeError('fixture')\n",
            "# fixture repaired and accepted\nvalue = close / previous_close - 1\n",
        ]
        replies = [
            json.dumps({"missing_code": "missing"}),
            json.dumps({"code": sources[0]}),
            json.dumps(
                {
                    "output_format_decision": True,
                    "output_format_feedback": "fixed numeric frame",
                }
            ),
            "Task criterion rejected",
            json.dumps({"final_decision": False, "final_feedback": "wrong fixed task"}),
            json.dumps({"code": sources[1]}),
            "Repair execution failure",
            json.dumps({"final_decision": False, "final_feedback": "execution failed"}),
            json.dumps({"code": sources[2]}),
            json.dumps(
                {
                    "output_format_decision": True,
                    "output_format_feedback": "fixed numeric frame",
                }
            ),
            "Task criterion satisfied",
            json.dumps({"final_decision": True, "final_feedback": "accepted"}),
        ]

        class ProbeHost:
            def __init__(self):
                self.steps = []
                self.sources = []
                self.feedback = []
                self.requests = []
                self.logical = []
                self.reads = []
                self.cache = {}
                self.phase = -1
                self.embedding_calls = 0
                self.call_contexts = []

            def check_control(self):
                pass

            def begin_step(self, step):
                assert step == len(self.steps)
                self.steps.append(step)
                self.phase = step

            def publish_source(self, *, task_index, source, source_sha256):
                assert task_index == 0 and source == sources[self.phase]
                identity = UUID(int=self.phase + 1)
                self.sources.append((identity, source, source_sha256))
                return identity

            def reject_source(self, *, task_index):
                raise AssertionError(
                    "malformed JSON must be repaired before assignment"
                )

            def begin_evaluation(self, source_id):
                return uuid4()

            def execute_or_reuse(self, *, source_id, evaluation_ticket):
                self.reads.append(evaluation_ticket)
                if evaluation_ticket not in self.cache:
                    if source_id == UUID(int=2):
                        result = ("Fixture host stand-in: execution failed.", None)
                    else:
                        index = pd.MultiIndex.from_product(
                            [pd.date_range("2024-03-06", periods=8), ["A", "B", "C"]],
                            names=["datetime", "instrument"],
                        )
                        result = (
                            "Fixture host stand-in: valid values.",
                            pd.DataFrame({"factor": range(24)}, index=index),
                        )
                    self.cache[evaluation_ticket] = result
                return self.cache[evaluation_ticket]

            def finish_evaluation(self, *, source_id, evaluation_ticket, feedback):
                self.feedback.append(feedback)

            def begin_exchange(self, *, kind, phase):
                identity = uuid4()
                frames = [
                    f.function for f in inspect.stack() if "rdagent/" in f.filename
                ]
                self.call_contexts.append(
                    {"step": self.phase, "kind": kind, "phase": phase, "frames": frames}
                )
                self.logical.append((identity, kind))
                return identity

            def new_request(self, exchange_id, *, relation):
                assert relation == "initial"
                return uuid4()

            def semantic_retry(self, logical_id, *, reason):
                raise AssertionError("unexpected semantic retry")

            def end_exchange(self, logical_id, *, succeeded):
                assert succeeded

            def count_tokens(self, messages):
                return sum(len(m["content"]) for m in messages) // 3

            def chat(self, logical_id, *, messages, response_format):
                self.requests.append(messages)
                if not replies:
                    raise BrokerAbort("request_drift")
                return replies.pop(0), "stop"

            def embedding(self, logical_id, *, inputs):
                self.embedding_calls += 1
                return [[1.0, 0.0] for _ in inputs]

        host = ProbeHost()
        task = FactorTask(
            factor_name=config.task.name,
            factor_description=config.task.description,
            factor_formulation=config.task.formulation,
            variables={"close": "Frozen close price"},
        )
        with use_host_broker(host):
            adapter = MineAgentFactorCoSTEER(config, host)
            result = adapter.develop(Experiment(sub_tasks=[task]))
        assert host.steps == [0, 1, 2] and len(host.sources) == 3 and not replies
        assert [f["final_decision"] for f in host.feedback] == [False, False, True]
        assert result.sub_workspace_list[0].source_id == UUID(int=3)
        assert len(host.cache) == 3 and len(host.reads) == 11
        assert len(host.requests) == 12 and host.embedding_calls == 5
        assert not list(Path(tmp).rglob("*.pkl"))
        if sys.flags.isolated:
            for name, module in sys.modules.items():
                if name.startswith(
                    ("rdagent.", "packages.rdagent_adapter.", "runtimes.rdagent.")
                ) and getattr(module, "__file__", None):
                    assert "/site-packages/" in str(Path(module.__file__).resolve())
            assert str(original_cwd) not in sys.path
        expected = [
            (0, "generation"),
            (0, "generation"),
            (0, "output_format"),
            (0, "critique"),
            (0, "final_decision"),
            (1, "generation"),
            (1, "critique"),
            (1, "final_decision"),
            (2, "generation"),
            (2, "output_format"),
            (2, "critique"),
            (2, "final_decision"),
        ] + [(2, "knowledge_update")] * 5
        assert [(c["step"], c["phase"]) for c in host.call_contexts] == expected
        assert attempts == {"network": 0, "pickle": 0}, attempts
        report = {
            "socket_connect_attempts": attempts["network"],
            "pickle_access_attempts": attempts["pickle"],
            "isolated_python": bool(sys.flags.isolated),
            "module_paths_installed_only": bool(sys.flags.isolated),
            "scope": "actual_costeer_control_flow_with_host_standins",
            "call_contexts": host.call_contexts,
            "upstream_modules": sorted(
                str(
                    Path(m.__file__).relative_to(
                        Path(__import__("rdagent").__path__[0]).parent
                    )
                )
                for n, m in sys.modules.items()
                if n.startswith("rdagent.") and getattr(m, "__file__", None)
            ),
            "steps": host.steps,
            "final_decisions": [False, False, True],
            "source_versions": 3,
            "malformed_response_repaired": True,
            "model_chat_calls": len(host.requests),
            "embedding_calls": host.embedding_calls,
            "logical_evaluations": len(host.cache),
            "repeated_value_reads": len(host.reads),
            "max_message_chars": max(
                len(m["content"]) for req in host.requests for m in req
            ),
            "max_request_chars": max(
                sum(len(m["content"]) for m in req) for req in host.requests
            ),
            "actual_candidate_execution": False,
            "durable_broker": False,
            "isolation": False,
        }
        print(json.dumps(report))
        os.chdir(original_cwd)


if __name__ == "__main__":
    main()
