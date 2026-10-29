"""Actual pinned SDK develop + token/send hook; host execution/provider are doubles.

No candidate source is executed. No network, container, SQL or live-provider proof.
"""

import argparse
import inspect
import json
import socket
import tempfile
from pathlib import Path
from uuid import UUID, uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument(
        "--mode",
        choices=("normal", "repair", "outer_repair", "continuation"),
        default="normal",
    )
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in offline SDK proof")

    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    from runtimes.rdagent.bootstrap import configure_upstream

    with tempfile.TemporaryDirectory(prefix="workflow-sdk-") as temporary:
        configure_upstream(Path(temporary))
        import pandas as pd
        from rdagent.components.coder.factor_coder.factor import FactorTask
        from rdagent.core.experiment import Experiment
        from rdagent.oai.llm_conf import LLM_SETTINGS

        from packages.rdagent_adapter.broker_bridge import use_host_broker
        from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
        from packages.rdagent_adapter.workflow_config import (
            WorkflowFactorResearchConfig,
        )
        from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
        from packages.research_runtime.control import BrokerAbort
        from runtimes.rdagent_operational.workflow_scenario import (
            WorkflowOperationalCoSTEER,
        )

        config = WorkflowFactorResearchConfig.model_validate(data["config"])
        seed = ResolvedSeedParent.model_validate(data["seed"]) if data["seed"] else None
        expected = first_workflow_messages(config, seed)
        sources = ["# Offline SDK host stand-in, never executed\nvalue = 1.0\n"]
        replies = [
            json.dumps({"code": sources[0]}),
            json.dumps(
                {
                    "output_format_decision": True,
                    "output_format_feedback": "fixed numeric frame",
                }
            ),
            "Task criterion satisfied",
            json.dumps({"final_decision": True, "final_feedback": "accepted"}),
        ]

        if args.mode == "repair":
            replies.insert(0, "{malformed JSON")
        elif args.mode == "outer_repair":
            replies.insert(0, json.dumps({"missing_code": "bounded SDK retry"}))
        elif args.mode == "continuation":
            complete = replies.pop(0)
            middle = len(complete) // 2
            replies[:0] = [(complete[:middle], "length"), (complete[middle:], "stop")]

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
                self.counted = []
                self.request_phases = []
                self.relations = []
                self.repairs = []

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
                assert relation in {"initial", "continuation", "semantic_repair"}
                self.relations.append(relation)
                return uuid4()

            def semantic_retry(self, logical_id, *, reason):
                assert args.mode == "repair"
                self.repairs.append(reason)

            def end_exchange(self, logical_id, *, succeeded):
                assert succeeded

            def count_tokens(self, messages):
                self.counted.append(messages)
                return sum(len(m["content"]) for m in messages) // 3

            def chat(self, logical_id, *, messages, response_format):
                self.requests.append(json.loads(json.dumps(messages)))
                self.request_phases.append(self.call_contexts[-1]["phase"])
                if not replies:
                    raise BrokerAbort("request_drift")
                reply = replies.pop(0)
                return reply if isinstance(reply, tuple) else (reply, "stop")

            def embedding(self, logical_id, *, inputs):
                self.embedding_calls += 1
                return [[1.0, 0.0] for _ in inputs]

        host = ProbeHost()
        from packages.rdagent_adapter.broker_bridge import model_phase
        from packages.rdagent_adapter.workflow_prompt import workflow_sdk_base_messages
        from runtimes.rdagent_operational.workflow_scenario import (
            _GENERATION_MESSAGES,
            WorkflowAPIBackend,
        )

        # Actual SDK message builder rejects missing scope, altered initial input,
        # and oversized final user content before any provider call.
        if config.workflow_prompt_layout is not None:
            base = workflow_sdk_base_messages(config, seed)
            with use_host_broker(host):
                backend = WorkflowAPIBackend()
                try:
                    backend._build_messages(user_prompt=base[1], system_prompt=base[0])
                    raise AssertionError("missing generation scope accepted")
                except BrokerAbort:
                    pass
                token = _GENERATION_MESSAGES.set((base, expected))
                try:
                    with model_phase("generation"):
                        try:
                            backend.build_messages_and_calculate_token(
                                user_prompt=base[1] + " changed", system_prompt=base[0]
                            )
                            raise AssertionError(
                                "changed SDK generation input accepted"
                            )
                        except BrokerAbort:
                            pass
                    with model_phase("critique"):
                        assert backend._build_messages(
                            user_prompt="original critique",
                            system_prompt="original rules",
                        ) == [
                            {"role": "system", "content": "original rules"},
                            {"role": "user", "content": "original critique"},
                        ]
                finally:
                    _GENERATION_MESSAGES.reset(token)
                token = _GENERATION_MESSAGES.set((base, (expected[0], "x" * 8193)))
                try:
                    with model_phase("generation"):
                        try:
                            backend.build_messages_and_calculate_token(
                                user_prompt=base[1], system_prompt=base[0]
                            )
                            raise AssertionError(
                                "oversized generation user message accepted"
                            )
                        except BrokerAbort:
                            pass
                finally:
                    _GENERATION_MESSAGES.reset(token)
            assert not host.requests and not host.logical and not host.counted
        task = FactorTask(
            factor_name=config.task.name,
            factor_description=config.task.description,
            factor_formulation=config.task.formulation,
            variables={
                field.name: f"Frozen {field.name} ({field.unit})"
                for field in config.input.input_fields
            },
        )
        with use_host_broker(host):
            engine = WorkflowOperationalCoSTEER(config, host, seed)
            result = engine.develop(Experiment(sub_tasks=[task]))
        assert host.steps == [0] and len(host.sources) == 1 and not replies
        assert len(host.requests) == (4 if args.mode == "normal" else 5)
        assert bool(host.repairs) == (args.mode == "repair")
        if args.mode == "continuation":
            assert "continuation" in host.relations
        actual = host.requests[0]
        assert actual == [
            {"role": "system", "content": expected[0]},
            {"role": "user", "content": expected[1]},
        ]
        assert actual in host.counted, "SDK token estimate must see exact sent evidence"
        assert all(
            len(m["content"]) <= 8192 for request in host.requests for m in request
        )
        marker = "mineagent.workflow-generation-user-evidence.v1"
        assert (marker in actual[1]["content"]) == (
            config.workflow_prompt_layout == "generation_user.v1"
        )
        assert all(
            marker not in message["content"]
            for phase, request in zip(host.request_phases, host.requests)
            if phase != "generation"
            for message in request
        )
        if config.workflow_prompt_layout is not None:
            for phase, request in zip(host.request_phases, host.requests):
                if phase == "generation":
                    assert sum(marker in message["content"] for message in request) == 1
                    assert expected[1] in request[1]["content"]
        assert (
            LLM_SETTINGS.backend == "packages.rdagent_adapter.broker_bridge.APIBackend"
        )
        assert result.sub_workspace_list[0].source_id == UUID(int=1)
        print(
            json.dumps(
                {
                    "actual_sdk_develop": True,
                    "mode": args.mode,
                    "repair_count": len(host.repairs),
                    "relations": host.relations,
                    "generation_messages_exact": True,
                    "token_estimate_equals_sent_messages": True,
                    "chat_calls": len(host.requests),
                    "embedding_calls": host.embedding_calls,
                    "generation_message_characters": list(map(len, expected)),
                    "other_phases_unchanged": True,
                    "host_execution": "offline_double_not_candidate_execution",
                }
            )
        )


if __name__ == "__main__":
    main()
