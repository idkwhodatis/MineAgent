"""Actual installed FactorCoSTEER prompt capture; no generated code is executed.

Local draft adapter imports, not installed image or durable broker acceptance.
The host captures exact upstream messages; SDK/PG integration is a separate gate.
"""

import json
import socket
import tempfile
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

attempts = []


def forbidden(*args, **kwargs):
    attempts.append(1)
    raise AssertionError("network forbidden")


socket.socket.connect = socket.socket.connect_ex = socket.create_connection = forbidden

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
        "pricing": {"status": "unavailable"},
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
        ProviderOperationalCoSTEER,
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

    class Host:
        def __init__(self):
            self.requests = []

        def check_control(self):
            pass

        def begin_exchange(self, **kw):
            return uuid4()

        def end_exchange(self, *args, **kw):
            assert kw["succeeded"]

        def new_request(self, *args, **kw):
            return uuid4()

        def count_tokens(self, messages):
            return sum(len(m["content"]) for m in messages) // 3

        def semantic_retry(self, *args, **kw):
            raise AssertionError("unexpected repair")

        def chat(self, *args, messages, response_format):
            self.requests.append(messages)
            return json.dumps({"code": "# captured only, never executed\n"}), "stop"

    host = Host()
    with use_host_broker(host), model_phase("generation"):
        result = FactorMultiProcessEvolvingStrategy(
            scen=ProviderGenerationScenario(config), settings=FACTOR_COSTEER_SETTINGS
        ).implement_one_task(task, knowledge)
    assert result == "# captured only, never executed\n"
    assert len(host.requests) == 1
    assert tuple(m["content"] for m in host.requests[0]) == first_provider_messages(
        config
    )
    assert text in host.requests[0][0]["content"]
    assert str(version.version_id) in host.requests[0][0]["content"]
    runtime = ProviderOperationalCoSTEER(config, host)
    assert str(
        version.version_id
    ) in runtime.evolving_strategy.scen.get_scenario_all_desc(task)
    assert str(version.version_id) not in runtime.scen.get_scenario_all_desc(task)
    assert not attempts
    print(
        json.dumps(
            {
                "actual_upstream_generation_prompt": True,
                "non_momentum_hypothesis": True,
                "exact_steer_version_consumed": True,
                "preflight_byte_equal": True,
                "network_attempts": len(attempts),
                "generated_source_executed": False,
                "durable_broker_acceptance": False,
            }
        )
    )
