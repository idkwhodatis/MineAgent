"""Actual installed CoSTEER + production provider client/SDK against localhost.

Local draft adapter imports, not installed image or durable broker acceptance.
The host captures exact upstream messages; SDK/PG integration is a separate gate.
"""

import json
import sys
import tempfile
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

FULL_LOOP = "--full-loop" in sys.argv
FAIL_EVALUATION = "--failed-evaluation" in sys.argv
REJECTION_FRAME = "--rejection-frame" in sys.argv
assert not REJECTION_FRAME or (FULL_LOOP and FAIL_EVALUATION)
active_phase = "generation"
phase_trace = []
feedback_seen = []
upstream_failure = None
attempts = []
requests_seen = []
GENERATED = "def downside_dispersion(close):\n    mean = sum(close) / len(close)\n    return sum(min(x-mean, 0)**2 for x in close) / len(close)\n"


class LocalProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path in {"/v1/chat/completions", "/v1/embeddings"}
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        document = json.loads(raw)
        requests_seen.append(document)
        content = json.dumps({"code": GENERATED})
        if active_phase == "output_format":
            content = json.dumps(
                {
                    "output_format_decision": True,
                    "output_format_feedback": "Explicit numeric host double",
                }
            )
        elif active_phase == "critique":
            content = "Actual model response to explicitly simulated numeric feedback."
        elif active_phase == "final_decision":
            content = json.dumps(
                {
                    "final_decision": not FAIL_EVALUATION,
                    "final_feedback": "Explicit simulated evaluation outcome",
                }
            )
        response_doc = {
            "id": "synthetic-generated-code",
            "object": "chat.completion",
            "created": 1,
            "model": "chat",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        if self.path == "/v1/embeddings":
            response_doc = {
                "object": "list",
                "model": "embed",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [1.0, 0.0]}
                    for index, _ in enumerate(document["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            }
        response = json.dumps(response_doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), LocalProvider)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion
from runtimes.rdagent.bootstrap import configure_upstream
from runtimes.rdagent.probe_fixture import research_dict

text = "Research downside dispersion of close prices; do not substitute momentum."
version = SteerVersion(
    steer_id=uuid4(),
    version_id=uuid4(),
    ordinal=1,
    kind="hypothesis",
    text=text,
    text_sha256=sha256(text.encode()).hexdigest(),
)
snapshot = SteerSnapshot(owner_id=uuid4(), versions=(version,))
raw = research_dict()
raw.update(
    schema_version="mineagent.factor-research.v2",
    max_iterations=1,
    model_profile="configured.rdagent.v1",
    steer_snapshot=snapshot.model_dump(mode="json"),
    steer_snapshot_sha256=snapshot.sha256,
    model_binding={
        "profile_id": "offline",
        "profile_sha256": "a" * 64,
        "chat_model": "chat",
        "embedding_model": "embed",
        "embedding_dimensions": 2,
        "max_output_tokens": 100,
        "request_deadline_ms": 8000,
        "synthetic": True,
        "pricing": {
            "status": "operator_declared",
            "chat_input_microusd_per_million": 1,
            "chat_output_microusd_per_million": 1,
            "embedding_microusd_per_million": 1,
        },
    },
)
raw["task"] = {
    **raw["task"],
    "name": "downside",
    "description": text,
    "formulation": "mean(min(close[t]-mean(close),0)^2)",
    "output_unit": "price_squared",
}
config = ProviderFactorResearchConfig.model_validate(raw)
with tempfile.TemporaryDirectory() as tmp:
    configure_upstream(Path(tmp))
    from rdagent.components.coder.CoSTEER.knowledge_management import (
        CoSTEERQueriedKnowledgeV1,
    )
    from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS
    from rdagent.components.coder.factor_coder.evolving_strategy import (
        FactorMultiProcessEvolvingStrategy,
    )
    from rdagent.components.coder.factor_coder.factor import FactorTask

    from packages.rdagent_adapter.broker_bridge import model_phase, use_host_broker
    from packages.rdagent_adapter.provider_prompt import first_provider_messages
    from runtimes.rdagent_operational.provider_scenario import (
        ProviderGenerationScenario,
    )

    task = FactorTask(
        factor_name=config.task.name,
        factor_description=config.task.description,
        factor_formulation=config.task.formulation,
        variables={
            f.name: f"Frozen {f.name} ({f.unit})" for f in config.input.input_fields
        },
    )
    info = task.get_task_information()
    knowledge = CoSTEERQueriedKnowledgeV1(
        task_to_former_failed_traces={info: ([], None)},
        task_to_similar_task_successful_knowledge={info: []},
    )

    from packages.rdagent_provider.codec import parse_request
    from packages.rdagent_provider.contracts import (
        RDProviderChatResponse,
        RDProviderEmbeddingResponse,
        RDProviderUsage,
    )
    from packages.rdagent_provider.transport import execute
    from runtimes.rdagent_operational.provider_client import ProviderOperationalHost

    class LocalChannel:
        def call(self, method, body):
            global active_phase
            if method == "check_control":
                return {"current": True}
            if method == "begin_step":
                assert body["step"] == 0
                return {"start_id": str(uuid4())}
            if method == "begin_exchange":
                active_phase = body["phase"]
                phase_trace.append((body["phase"], body["kind"]))
                return {"exchange_id": str(uuid4())}
            if method == "end_exchange":
                return {}
            if method == "execute_or_reuse" and REJECTION_FRAME:
                from packages.rdagent_adapter.numeric_failure import (
                    RDNumericFailureFrame,
                )

                return RDNumericFailureFrame(
                    source_id=body["source_id"],
                    evaluation_id=body["evaluation_id"],
                    factor_version_id=uuid4(),
                    cutoff_execution_id=uuid4(),
                    candidate_admission_id=uuid4(),
                    rejection_receipt_id=uuid4(),
                    stop_receipt_id=uuid4(),
                    rejection_sha256="a" * 64,
                    stop_sha256="b" * 64,
                    outcome="child_nonzero",
                    child_exit_code=1,
                ).model_dump(mode="json")
            assert method in {"submit_chat", "submit_embedding"}
            request = parse_request(body["protocol"], body["request_utf8"].encode())
            result = execute(
                request,
                binding=config.model_binding,
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                secret="synthetic-not-a-real-credential",
                deadline_monotonic=time.monotonic() + 8,
                python_executable=sys.executable,
            )
            assert (
                result.stop_evidence.leader_reaped
                and result.stop_evidence.process_group_absent
            )
            usage = RDProviderUsage(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
                usage_source=result.usage_source,
                provider_request_id=result.provider_request_id,
            )
            response = (
                RDProviderChatResponse(
                    content=result.content,
                    finish_reason=result.finish_reason,
                    usage=usage,
                )
                if method == "submit_chat"
                else RDProviderEmbeddingResponse(vectors=result.vectors, usage=usage)
            )
            return {
                "operation_id": str(uuid4()),
                "response": response.model_dump(mode="json"),
            }

    class ExplicitNumericDoubleHost(ProviderOperationalHost):
        def publish_source(self, *, task_index, source, source_sha256):
            assert task_index == 0 and source == GENERATED
            Path(tmp, "actual-generated-source.py").write_text(source)
            self.last_source = uuid4()
            return self.last_source

        def begin_evaluation(self, source_id):
            assert source_id == self.last_source
            return uuid4()

        def execute_or_reuse(self, *, source_id, evaluation_ticket):
            assert source_id == self.last_source
            if REJECTION_FRAME:
                return super().execute_or_reuse(
                    source_id=source_id, evaluation_ticket=evaluation_ticket
                )
            if FAIL_EVALUATION:
                return (
                    "Explicit numeric host double: simulated ABI rejection; code not executed",
                    None,
                )
            import pandas as pd

            index = pd.MultiIndex.from_product(
                [pd.date_range("2024-03-06", periods=8), ["A", "B", "C"]],
                names=["datetime", "instrument"],
            )
            return "Explicit numeric host double; not source execution", pd.DataFrame(
                {"factor": range(24)}, index=index
            )

        def finish_evaluation(self, *, source_id, evaluation_ticket, feedback):
            feedback_seen.append(feedback)
            Path(tmp, "actual-upstream-feedback.json").write_text(json.dumps(feedback))

    if FULL_LOOP:
        from rdagent.core.exception import CoderError
        from rdagent.core.experiment import Experiment

        from runtimes.rdagent_operational.provider_scenario import (
            ProviderOperationalCoSTEER,
        )

        host = ExplicitNumericDoubleHost(LocalChannel(), config.model_binding)
        try:
            with use_host_broker(host):
                ProviderOperationalCoSTEER(config, host).develop(
                    Experiment(sub_tasks=[task])
                )
        except CoderError as exc:
            if not FAIL_EVALUATION:
                raise
            upstream_failure = type(exc).__name__
        assert (upstream_failure == "CoderError") is FAIL_EVALUATION
        result = Path(tmp, "actual-generated-source.py").read_text()
        assert len(feedback_seen) == 1
        assert feedback_seen[0]["final_decision"] is (not FAIL_EVALUATION)
        if not FAIL_EVALUATION:
            assert any(
                phase == "knowledge_update" and kind == "embedding"
                for phase, kind in phase_trace
            )
    else:
        host = ProviderOperationalHost(LocalChannel(), config.model_binding)
        host.step = uuid4()
        with use_host_broker(host), model_phase("generation"):
            result = FactorMultiProcessEvolvingStrategy(
                scen=ProviderGenerationScenario(config),
                settings=FACTOR_COSTEER_SETTINGS,
            ).implement_one_task(task, knowledge)
    assert result == GENERATED
    assert FULL_LOOP or len(requests_seen) == 1
    messages = requests_seen[0]["messages"]
    assert tuple(m["content"] for m in messages) == first_provider_messages(config)
    assert (
        text in messages[0]["content"]
        and str(version.version_id) in messages[0]["content"]
    )
    assert "downside_dispersion" in result and "close[t]/close" not in result
    Path(tmp, "actual-generated-source.py").write_text(result)
    assert Path(tmp, "actual-generated-source.py").read_text() == GENERATED
    print(
        json.dumps(
            {
                "actual_upstream_generation": True,
                "actual_upstream_develop": FULL_LOOP,
                "phase_trace": phase_trace,
                "numeric_evaluation_is_explicit_double": FULL_LOOP,
                "actual_upstream_feedback": feedback_seen,
                "failed_evaluation_double": FAIL_EVALUATION,
                "configured_rejection_frame_decoder": REJECTION_FRAME,
                "upstream_failure": upstream_failure,
                "production_provider_client": True,
                "real_sdk_localhost_calls": len(requests_seen),
                "non_momentum_source_sha256": sha256(result.encode()).hexdigest(),
                "steer_version_consumed": True,
                "source_executed": False,
                "durable_broker_acceptance": False,
                "provider_billing": "unknown",
            }
        )
    )
server.shutdown()
thread.join(timeout=2)
server.server_close()
