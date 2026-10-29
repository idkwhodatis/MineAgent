"""Fixed single-generation installed controller entrypoint; no standalone authority.

Only admitted bootstrap + host IPC are read. All model work, source publication,
candidate execution, normalized data and finalization are host transactions.
The real pinned CoSTEER loop runs here; no generated source is executed here.
"""

import json
import os
import socket
import stat
import sys
import time
from pathlib import Path

from packages.rdagent_adapter.controller_profile import (
    COMPOSED_CONTROLLER_PROFILE,
    LEGACY_CONTROLLER_PROFILE,
    SEEDED_CONTROLLER_PROFILE,
    controller_profile,
)
from packages.rdagent_adapter.seeded_bootstrap import (
    RDSeededOperationalBootstrap,
    parse_bootstrap,
)
from runtimes.rdagent_operational.client import OperationalHost
from runtimes.rdagent_operational.deadline import remaining_seconds
from runtimes.rdagent_operational.rpc import Client, RPCError


def read_fixed(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise RPCError("fixed_input")
        raw = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise RPCError("fixed_input_drift")
        return raw
    finally:
        os.close(descriptor)


def main():
    if (
        not sys.flags.isolated
        or len(sys.argv) not in (1, 3)
        or os.getresuid() != (65533,) * 3
        or os.getresgid() != (65533,) * 3
    ):
        raise RPCError("fixed_runtime")
    if set(os.listdir("/input")) != {"bootstrap.json"} or set(os.listdir("/ipc")) != {
        "ticket.json",
        "peer.sock",
    }:
        raise RPCError("fixed_input_layout")
    raw = read_fixed("/input/bootstrap.json", 262144)
    bootstrap = parse_bootstrap(raw)
    from packages.factors.contracts import canonical_bytes

    if canonical_bytes(bootstrap) != raw or bootstrap.config.max_iterations != 1:
        raise RPCError("bootstrap")
    selected_profile = controller_profile(bootstrap.config)
    if len(sys.argv) == 1:
        if selected_profile != LEGACY_CONTROLLER_PROFILE:
            raise RPCError("controller_profile_mismatch")
        rpc_deadline = time.monotonic() + 60
    else:
        if (
            sys.argv[1] not in (COMPOSED_CONTROLLER_PROFILE, SEEDED_CONTROLLER_PROFILE)
            or selected_profile != sys.argv[1]
        ):
            raise RPCError("controller_profile_mismatch")
        rpc_deadline = time.monotonic() + remaining_seconds(
            sys.argv[2], time.time_ns() // 1000000, profile=selected_profile
        )
    ticket = json.loads(read_fixed("/ipc/ticket.json", 256))
    if type(ticket) is not dict or set(ticket) != {"ticket"}:
        raise RPCError("ticket")
    workspace = Path("/tmp/rd-workspace")
    workspace.mkdir(mode=0o700)
    from runtimes.rdagent.bootstrap import configure_upstream

    configure_upstream(workspace)
    from rdagent.components.coder.factor_coder.factor import FactorTask
    from rdagent.core.experiment import Experiment

    from packages.rdagent_adapter.broker_bridge import use_host_broker
    from runtimes.rdagent_operational.adapter import OperationalCoSTEER

    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(10)
        channel.connect("/ipc/peer.sock")
        client = Client(channel, ticket["ticket"], deadline=rpc_deadline)
        config = bootstrap.config
        from packages.rdagent_adapter.provider_config import (
            ProviderFactorResearchConfig,
        )

        if isinstance(config, ProviderFactorResearchConfig):
            from runtimes.rdagent_operational.provider_client import (
                ProviderOperationalHost,
            )

            host = ProviderOperationalHost(client, config.model_binding)
        else:
            host = OperationalHost(client)
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
            if isinstance(config, ProviderFactorResearchConfig):
                from runtimes.rdagent_operational.provider_scenario import (
                    ProviderOperationalCoSTEER,
                )

                engine = ProviderOperationalCoSTEER(
                    config, host, getattr(bootstrap, "seed", None)
                )
            elif isinstance(bootstrap, RDSeededOperationalBootstrap):
                from runtimes.rdagent_operational.seeded_scenario import (
                    SeededOperationalCoSTEER,
                )

                engine = SeededOperationalCoSTEER(config, host, bootstrap.seed)
            else:
                engine = OperationalCoSTEER(config, host)
            result = engine.develop(Experiment(sub_tasks=[task]))
        if len(result.sub_workspace_list) != 1:
            raise RPCError("fixed_task")
        selected = result.sub_workspace_list[0].source_id
        if selected is None or selected != host.last_source:
            raise RPCError("single_generation_adoption")
        client.call(
            "generation_boundary",
            {
                "start_id": str(host.step),
                "logical_step": 0,
                "evaluated_source_id": str(host.last_source),
                "previous_adopted_source_id": None,
                "adopted_source_id": str(selected),
                "continuation_source_id": str(host.last_source),
                "adopted_new": True,
                "boundary": "returned",
                "next_logical_step": None,
            },
        )


if __name__ == "__main__":
    main()
