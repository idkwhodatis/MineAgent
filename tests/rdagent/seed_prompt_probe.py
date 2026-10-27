"""Known-input upstream prompt probe; run with the pinned RD dependency env.

PYTHONPATH=. ../mineagent-rdagent-locked/bin/python tests/rdagent/seed_prompt_probe.py
This uses local draft adapter modules and installed upstream. It is NOT an
installed adapter wheel, Docker, durable broker, PG or execution acceptance test.
Only the maintained source is read as prompt data; generated code is not run.
"""

import json
import socket
import tempfile
from pathlib import Path
from uuid import uuid4

attempts = []


def forbidden(*a, **k):
    attempts.append("network")
    raise AssertionError("network forbidden")


socket.socket.connect = socket.socket.connect_ex = socket.create_connection = forbidden
from runtimes.rdagent.bootstrap import configure_upstream
from runtimes.rdagent.probe_fixture import research_dict

root = Path.cwd()
source = (root / "runtimes/factor_execution/abi_fixture.py").read_text()
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

    from packages.factors.contracts import FactorDefinition, InputField, source_sha256
    from packages.rdagent_adapter.broker_bridge import model_phase, use_host_broker
    from packages.rdagent_adapter.contracts import FactorResearchConfig
    from packages.rdagent_adapter.seed_parent import (
        ResolvedSeedParent,
        SeedParentReference,
    )
    from runtimes.rdagent_operational.seeded_scenario import SeededOperationalScenario

    sha = source_sha256(source)
    definition = FactorDefinition(
        code_sha256=sha,
        factor_kind="time_series",
        input_fields=(
            InputField(name="close", unit="USD", price_semantics="unadjusted"),
        ),
        lookback_sessions=2,
        output_unit="return",
    )
    ref = SeedParentReference(
        owner_id=uuid4(),
        factor_id=uuid4(),
        factor_version_id=uuid4(),
        ordinal=1,
        code_object_id=uuid4(),
        code_sha256=sha,
        code_size_bytes=len(source.encode()),
        definition_sha256=definition.sha256,
        source_provenance_sha256="a" * 64,
    )
    seed = ResolvedSeedParent(reference=ref, definition=definition, source=source)
    config = FactorResearchConfig.model_validate(
        {**research_dict(), "max_iterations": 1}
    )
    scen = SeededOperationalScenario(config, seed)
    task = FactorTask(
        factor_name=config.task.name,
        factor_description=config.task.description,
        factor_formulation=config.task.formulation,
        variables={
            field.name: f"Frozen {field.name} ({field.unit})"
            for field in config.input.input_fields
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

        def end_exchange(self, *a, **kw):
            assert kw["succeeded"]

        def new_request(self, *a, **kw):
            return uuid4()

        def count_tokens(self, messages):
            return sum(len(m["content"]) for m in messages) // 3

        def semantic_retry(self, *a, **kw):
            raise AssertionError("unexpected repair")

        def chat(self, *a, messages, response_format):
            self.requests.append(messages)
            return json.dumps({"code": "# not executed\n"}), "stop"

    host = Host()
    with use_host_broker(host), model_phase("generation"):
        result = FactorMultiProcessEvolvingStrategy(
            scen=scen, settings=FACTOR_COSTEER_SETTINGS
        ).implement_one_task(task, knowledge)
    assert result == "# not executed\n"
    assert len(host.requests) == 1 and not attempts
    messages = host.requests[0]
    from packages.rdagent_adapter.seed_prompt_budget import first_prompt_messages

    expected = first_prompt_messages(config, seed)
    assert tuple(message["content"] for message in messages) == expected
    assert any(seed.prompt_section() in m["content"] for m in messages)
    from runtimes.rdagent_operational.seeded_scenario import SeededOperationalCoSTEER

    runtime = SeededOperationalCoSTEER(config, host, seed)
    assert (
        seed.prompt_section()
        in runtime.evolving_strategy.scen.get_scenario_all_desc(task)
    )
    assert seed.prompt_section() not in runtime.scen.get_scenario_all_desc(task)
    assert not attempts
    print(
        json.dumps(
            {
                "actual_upstream_first_generation": True,
                "pure_preflight_messages_byte_equal": True,
                "seed_only_in_generation_scenario": True,
                "message_lengths": [len(m["content"]) for m in messages],
                "seed_section_bytes": len(seed.prompt_section().encode()),
                "network_attempts": len(attempts),
                "source_executed": False,
                "max8192": all(len(m["content"]) <= 8192 for m in messages),
            }
        )
    )
