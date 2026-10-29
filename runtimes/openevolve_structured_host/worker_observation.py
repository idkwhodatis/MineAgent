"""027 exact controller-attested pool member mapped through real kernel data.

No arbitrary descendant or first ticket holder is selected. The trusted installed
native scheduler supplies namespace PID/start; host maps it in the exact current
container, confirms direct lineage/UID and rechecks protected leader/controller.
"""

import time

from packages.openevolve_structured.host_events import WorkerPlacementObservation
from runtimes.openevolve_operational.daemon_identity import (
    TOP_FIELDS,
    DaemonIdentityContext,
    _inspect,
    parse_top,
)
from runtimes.openevolve_structured_host.peer import ControllerBinding
from runtimes.research_supervisor.supervisor import DockerSupervisor
from runtimes.research_supervisor.worker_placement import _status


def observe_worker(context, binding, placement):
    if (
        type(context) is not DaemonIdentityContext
        or type(binding) is not ControllerBinding
        or type(placement) is not WorkerPlacementObservation
    ):
        raise ValueError("exact worker observation context")
    if (
        context.container_id != binding.container_id
        or context.leader_pid != binding.leader.host_pid
    ):
        raise ValueError("worker container identity")
    pair = (binding.leader, binding.controller)
    if context.observe_pair(binding.controller.host_pid) != pair:
        raise ValueError("worker controller changed")
    parent, chain = _status(binding.controller.host_pid)
    if (
        parent != binding.leader.host_pid
        or chain[-1] != placement.controller_namespace_pid
    ):
        raise ValueError("worker controller namespace mapping")
    cli = DockerSupervisor(context.image_id)
    _inspect(cli, context.container_id, context.image_id, context.leader_pid)
    _, raw = cli._command(["top", context.container_id, "-eo", TOP_FIELDS])
    inventory = parse_top(raw)
    matches = []
    for pid, (uid, ppid, _) in inventory.items():
        if uid != 65533 or ppid != binding.controller.host_pid:
            continue
        try:
            parent, chain = _status(pid)
        except FileNotFoundError:
            continue
        if (
            parent == binding.controller.host_pid
            and chain[-1] == placement.worker_namespace_pid
        ):
            worker = context.observe(pid)
            parent_after, chain_after = _status(pid)
            if (
                (parent_after, chain_after) != (parent, chain)
                or worker.start_ticks != placement.worker_start_ticks
                or worker.ppid != parent
                or worker.uids != (65533,) * 4
                or worker.gids != (65533,) * 4
                or worker.pid_namespace != binding.controller.pid_namespace
                or worker.cgroup != binding.controller.cgroup
            ):
                raise ValueError("worker actual kernel identity drift")
            matches.append(worker)
    _inspect(cli, context.container_id, context.image_id, context.leader_pid)
    if (
        len(matches) != 1
        or context.observe_pair(binding.controller.host_pid) != pair
        or time.monotonic() >= context.deadline
    ):
        raise ValueError("unique live native pool assignment required")
    return matches[0]
