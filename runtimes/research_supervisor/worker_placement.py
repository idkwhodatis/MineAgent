"""Bounded kernel observation for a disabled trusted pool-worker placement seam.

This is evidence, NOT authorization or an issuer. The caller must obtain the PID
list from docker top on the exact durable container identity (never a client PID
list), and the expected namespace PID/start from its authenticated controller.
Persist only inside a fresh live-controller/fenced epoch transaction. No scanning
global /proc, ticket-first claiming, dispatcher, registry or container exec.
"""

from pathlib import Path

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.supervisor import process_identity

MAX_POOL_PIDS = 64


def _positive(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ProcessError("worker_placement_integer")
    return value


def _status(pid):
    with Path(f"/proc/{pid}/status").open("rb") as stream:
        raw = stream.read(16_385)
    if len(raw) > 16_384:
        raise ProcessError("worker_placement_status")
    fields = {}
    for line in raw.decode("ascii").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"PPid", "NSpid"}:
            if key in fields:
                raise ProcessError("worker_placement_status")
            fields[key] = value.split()
    if set(fields) != {"PPid", "NSpid"} or len(fields["PPid"]) != 1:
        raise ProcessError("worker_placement_status")
    tokens = fields["PPid"] + fields["NSpid"]
    if not 1 <= len(fields["NSpid"]) <= 32 or any(
        not x.isascii() or not x.isdecimal() or len(x) > 20 for x in tokens
    ):
        raise ProcessError("worker_placement_status")
    return int(fields["PPid"][0]), tuple(int(x) for x in fields["NSpid"])


def observe_pool_worker(
    controller_identity,
    namespace_pid,
    start_ticks,
    candidate_host_pids,
    *,
    controller_namespace_pid,
):
    """Observe exactly one current direct child, with before/after PID checks.

    A changed, incomplete or foreign unit listing fails closed. An exited entry
    may vanish, but cannot supply expected placement. Root/controller membership
    alone never substitutes for the controller-attested PID/start pair.
    """
    _positive(namespace_pid)
    _positive(start_ticks)
    _positive(controller_namespace_pid)
    if namespace_pid == controller_namespace_pid:
        raise ProcessError("worker_placement_parent")
    if type(controller_identity) is not dict or set(controller_identity) != {
        "host_pid",
        "start_ticks",
        "pid_namespace",
        "cgroup",
    }:
        raise ProcessError("worker_placement_controller")
    controller_pid = _positive(controller_identity["host_pid"])
    _positive(controller_identity["start_ticks"])
    if (
        type(candidate_host_pids) is not tuple
        or not 1 <= len(candidate_host_pids) <= MAX_POOL_PIDS
        or any(
            type(pid) is not int or not 0 < pid < 2**63 for pid in candidate_host_pids
        )
        or len(set(candidate_host_pids)) != len(candidate_host_pids)
        or controller_pid not in candidate_host_pids
    ):
        raise ProcessError("worker_placement_pid_inventory")
    if process_identity(controller_pid) != controller_identity:
        raise ProcessError("worker_placement_controller")
    matches = []
    for pid in candidate_host_pids:
        try:
            before = process_identity(pid)
            parent, chain = _status(pid)
            after = process_identity(pid)
        except FileNotFoundError:
            continue
        if before != after:
            raise ProcessError("worker_placement_reused")
        if (
            before["pid_namespace"] != controller_identity["pid_namespace"]
            or before["cgroup"] != controller_identity["cgroup"]
            or not chain
            or chain[0] != pid
            or any(n <= 0 for n in chain)
        ):
            raise ProcessError("worker_placement_unit")
        if pid == controller_pid and chain[-1] != controller_namespace_pid:
            raise ProcessError("worker_placement_controller")
        if chain[-1] == namespace_pid and before["start_ticks"] == start_ticks:
            if pid == controller_pid or parent != controller_pid:
                raise ProcessError("worker_placement_parent")
            matches.append(
                {**before, "namespace_pid": namespace_pid, "parent_host_pid": parent}
            )
    if process_identity(controller_pid) != controller_identity or len(matches) != 1:
        raise ProcessError("worker_placement_not_unique")
    # Refresh the selected worker after inspecting all peers; still not a permit
    # to omit the per-event live peer check in the subsequent domain transaction.
    selected = matches[0]
    if process_identity(selected["host_pid"]) != {
        key: selected[key] for key in controller_identity
    }:
        raise ProcessError("worker_placement_reused")
    return selected
