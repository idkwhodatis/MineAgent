"""Disabled phase8 observation only; never self-reported host worker authority."""

import os
from pathlib import Path
from uuid import UUID

from . import observer


def send_placement(payload):
    from .observer_transport import send_placement as send

    return send(payload)


def observe_submitted(executor, descriptor):
    with observer.control_guard("submission"):
        epoch = observer.require_trusted_population()
        if (
            type(descriptor) is not dict
            or descriptor.get("epoch_id") != str(epoch)
            or type(descriptor.get("intent_id")) is not str
            or str(UUID(descriptor["intent_id"])) != descriptor["intent_id"]
            or executor._max_workers != 1
            or type(executor._processes) is not dict
            or len(executor._processes) != 1
        ):
            raise ValueError("Exact single-worker submitted placement required")
        pid, process = next(iter(executor._processes.items()))
        if (
            type(pid) is not int
            or pid <= 0
            or process.pid != pid
            or not process.is_alive()
            or process.exitcode is not None
        ):
            raise ValueError("Submitted worker is not alive")
        stat_path = Path("/proc") / str(pid) / "stat"
        first = stat_path.read_text()
        start_ticks = int(first.rsplit(")", 1)[1].split()[19])
        second = stat_path.read_text()
        if (
            start_ticks <= 0
            or int(second.rsplit(")", 1)[1].split()[19]) != start_ticks
            or not process.is_alive()
        ):
            raise ValueError("Submitted worker identity changed")
        payload = {
            "schema_version": "mineagent.oe-structured-worker-placement.v1",
            "epoch_id": str(epoch),
            "intent_id": descriptor["intent_id"],
            "controller_namespace_pid": os.getpid(),
            "worker_namespace_pid": pid,
            "worker_start_ticks": start_ticks,
        }
        result = send_placement(payload)
        if (
            type(result) is not dict
            or set(result)
            != {
                "placement_receipt_id",
                "epoch_id",
                "intent_id",
                "worker_namespace_pid",
                "worker_start_ticks",
            }
            or str(UUID(result["placement_receipt_id"]))
            != result["placement_receipt_id"]
            or result["epoch_id"] != payload["epoch_id"]
            or result["intent_id"] != payload["intent_id"]
            or type(result["worker_namespace_pid"]) is not int
            or result["worker_namespace_pid"] != pid
            or type(result["worker_start_ticks"]) is not int
            or result["worker_start_ticks"] != start_ticks
        ):
            raise ValueError("Placement observation acknowledgement differs")
        return result
