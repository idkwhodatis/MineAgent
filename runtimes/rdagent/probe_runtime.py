"""Offline import/transport-seam probe, NOT an engine or containment acceptance."""

from __future__ import annotations

import builtins
import io
import json
import pickle
import socket
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def main():
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

    with tempfile.TemporaryDirectory() as workspace:
        upstream = configure_upstream(Path(workspace))
        from packages.rdagent_adapter.broker_bridge import (
            APIBackend,
            model_phase,
            use_host_broker,
        )
        from packages.research_runtime.control import BrokerAbort

        class ProbeHost:
            def __init__(self):
                self.exchanges = []
                self.logical = []
                self.calls = []
                self.completed = []
                self.aborted = False

            def check_control(self):
                if self.aborted:
                    raise BrokerAbort("cancelled")

            def begin_exchange(self, *, kind, phase):
                identity = uuid4()
                self.exchanges.append((identity, kind))
                return identity

            def new_request(self, exchange_id, *, relation):
                identity = uuid4()
                self.logical.append((identity, exchange_id, relation))
                return identity

            def semantic_retry(self, logical_id, *, reason):
                raise AssertionError("unexpected semantic retry")

            def end_exchange(self, logical_id, *, succeeded):
                self.completed.append((logical_id, succeeded))

            def chat(self, logical_id, *, messages, response_format):
                self.check_control()
                self.calls.append((logical_id, messages))
                _, exchange_id, _relation = next(
                    item for item in self.logical if item[0] == logical_id
                )
                count = sum(item[1] == exchange_id for item in self.logical)
                return (
                    '{"code":' if count == 1 else '"x=1"}',
                    "length" if count == 1 else "stop",
                )

            def embedding(self, logical_id, *, inputs):
                self.check_control()
                self.calls.append((logical_id, inputs))
                return [[1.0, 0.0] for _ in inputs]

            def count_tokens(self, messages):
                return sum(len(m["content"]) for m in messages)

        host = ProbeHost()
        with use_host_broker(host), model_phase("knowledge_query"):
            for _ in range(2):
                reply = APIBackend().build_messages_and_create_chat_completion(
                    user_prompt="Implement a fixture",
                    system_prompt="Return JSON",
                    json_mode=True,
                )
                assert json.loads(reply) == {"code": "x=1"}
            assert APIBackend().create_embedding(["frozen fixture"]) == [[1.0, 0.0]]
            host.aborted = True
            before = len(host.calls)
            try:
                APIBackend()
            except BrokerAbort:
                pass
            else:
                raise AssertionError("control abort was swallowed")
            assert len(host.calls) == before
        assert (
            len(host.exchanges) == 3 and len(host.logical) == 5 and len(host.calls) == 5
        )
        assert (
            host.calls[0][0] != host.calls[1][0]
            and host.logical[0][1] == host.logical[1][1]
            and host.logical[0][2] == "initial"
            and host.logical[1][2] == "continuation"
        )
        assert all(done for _, done in host.completed)

        class RepairHost(ProbeHost):
            def __init__(self, cancel=False):
                super().__init__()
                self.replies = ["not-json", '{"code": []}', '{"code": "x=1"}']
                self.repairs = []
                self.cancel = cancel

            def chat(self, logical_id, *, messages, response_format):
                self.check_control()
                self.calls.append((logical_id, messages))
                return self.replies.pop(0), "stop"

            def semantic_retry(self, logical_id, *, reason):
                self.repairs.append((logical_id, reason))
                if self.cancel:
                    self.aborted = True

        repaired = RepairHost()
        with use_host_broker(repaired), model_phase("generation"):
            result = APIBackend().build_messages_and_create_chat_completion(
                user_prompt="Fixed JSON fixture",
                system_prompt="Return JSON",
                json_mode=True,
                json_target_type=dict[str, str],
            )
            assert json.loads(result) == {"code": "x=1"}
        assert (
            len(repaired.exchanges) == 1
            and len(repaired.logical) == 3
            and len(repaired.calls) == 3
        )
        assert [item[2] for item in repaired.logical] == [
            "initial",
            "semantic_repair",
            "semantic_repair",
        ]
        assert len({call[0] for call in repaired.calls}) == 3
        assert [reason for _, reason in repaired.repairs] == [
            "invalid_json",
            "invalid_response_schema",
        ]
        assert "json" in repaired.calls[1][1][-1]["content"].lower()
        cancelled = RepairHost(cancel=True)
        with use_host_broker(cancelled), model_phase("generation"):
            try:
                APIBackend().build_messages_and_create_chat_completion(
                    user_prompt="Fixed JSON fixture",
                    system_prompt="Return JSON",
                    json_mode=True,
                )
            except BrokerAbort as error:
                assert error.code == "cancelled"
            else:
                raise AssertionError("semantic repair swallowed cancellation")
        assert len(cancelled.calls) == 1
        assert cancelled.completed[-1][1] is False
        assert not list(Path(workspace).rglob("*"))
        if sys.flags.isolated:
            for name, module in sys.modules.items():
                if name.startswith(
                    ("rdagent.", "packages.rdagent_adapter.", "runtimes.rdagent.")
                ) and getattr(module, "__file__", None):
                    assert "/site-packages/" in str(Path(module.__file__).resolve())
        assert attempts == {"network": 0, "pickle": 0}, attempts
        print(
            json.dumps(
                {
                    "scope": "imports_and_transport_bridge_only",
                    "isolated_python": bool(sys.flags.isolated),
                    "module_paths_installed_only": bool(sys.flags.isolated),
                    "upstream": upstream.__module__,
                    "upstream_call_contexts": 3,
                    "logical_requests": 5,
                    "semantic_repair_submissions": 3,
                    "cancelled_repair_submissions": 1,
                    "inner_submissions": 5,
                    "socket_connect_attempts": attempts["network"],
                    "pickle_access_attempts": attempts["pickle"],
                    "actual_candidate_execution": False,
                    "durable_broker": False,
                }
            )
        )


if __name__ == "__main__":
    main()
