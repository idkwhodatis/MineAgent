"""Actual single-worker placement/wait compatibility, explicit synthetic host.

Local proc identities are observed; no SO_PEERCRED listener or namespace mapping
acceptance is claimed. No database is imported or SQL lock held by this waiter.
"""

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from packages.openevolve_adapter import observer, placement_observer
from packages.openevolve_adapter.placement_contracts import WorkerPlacementObservation
from runtimes.openevolve_phase7 import full_loop_sampling_probe as phase7
from runtimes.research_supervisor.supervisor import process_identity
from runtimes.research_supervisor.worker_placement import observe_pool_worker

INSTALL = phase7.install
PLACEMENTS = []


def _root():
    return Path(os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"])


def _write(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.rename(path)


def namespace_pid(host_pid):
    rows = (Path("/proc") / str(host_pid) / "status").read_text().splitlines()
    values = [row.split()[1:] for row in rows if row.startswith("NSpid:")]
    assert len(values) == 1 and values[0]
    return int(values[0][-1])


def observe_controller(payload):
    WorkerPlacementObservation.model_validate_json(json.dumps(payload))
    # Synthetic host runs in the authenticated-controller position, using actual
    # kernel observations. Local namespace PID == host PID for this proof only.
    assert payload["controller_namespace_pid"] == os.getpid()
    pid = payload["worker_namespace_pid"]
    from multiprocessing.resource_tracker import _resource_tracker

    controller = process_identity(os.getpid())
    candidates = tuple(
        dict.fromkeys(
            [
                os.getpid(),
                pid,
                *([_resource_tracker._pid] if _resource_tracker._pid else []),
            ]
        )
    )
    selected = observe_pool_worker(
        controller,
        payload["worker_namespace_pid"],
        payload["worker_start_ticks"],
        candidates,
        controller_namespace_pid=payload["controller_namespace_pid"],
    )
    actual = {key: selected[key] for key in controller}
    assert (
        selected["namespace_pid"] == pid and selected["parent_host_pid"] == os.getpid()
    )
    if os.environ.get("MINEAGENT_PLACEMENT_PROBE_DENY") == "1":
        raise observer.SearchControlAbort("submission")
    receipt = {**payload, "observed": actual, "placement_receipt_id": str(uuid4())}
    _write(_root() / ("placement-" + payload["intent_id"] + ".json"), receipt)
    PLACEMENTS.append(receipt)
    return {
        key: receipt[key]
        for key in (
            "placement_receipt_id",
            "epoch_id",
            "intent_id",
            "worker_namespace_pid",
            "worker_start_ticks",
        )
    }


def await_expected(descriptor, *, seconds=2.0):
    deadline = time.monotonic() + seconds
    path = _root() / ("placement-" + descriptor["intent_id"] + ".json")
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise observer.SearchControlAbort("worker_observation")
        time.sleep(0.01)  # no SQL session/lock spans this bounded wait
    receipt = json.loads(path.read_text())
    actual = process_identity(os.getpid())
    assert receipt["epoch_id"] == descriptor["epoch_id"]
    assert receipt["intent_id"] == descriptor["intent_id"]
    assert receipt["worker_namespace_pid"] == namespace_pid(os.getpid())
    assert receipt["worker_start_ticks"] == actual["start_ticks"]
    assert receipt["observed"] == actual
    # Single worker may accept next intent only after prior explicit closure.
    for admitted in _root().glob("assigned-*.json"):
        identity = json.loads(admitted.read_text())["intent_id"]
        assert (_root() / ("closed-" + identity + ".json")).is_file()
    _write(
        _root() / ("assigned-" + descriptor["intent_id"] + ".json"),
        {"intent_id": descriptor["intent_id"], "host_pid": os.getpid()},
    )


def install():
    INSTALL()
    old_worker = observer.worker_scope

    @contextmanager
    def assigned(descriptor):
        await_expected(descriptor)
        with old_worker(descriptor):
            try:
                yield
            finally:
                _write(
                    _root() / ("closed-" + descriptor["intent_id"] + ".json"),
                    {"closed": True},
                )

    observer.worker_scope = assigned
    placement_observer.send_placement = observe_controller


phase7.install = install
phase7.loop.install_explicit_doubles = install
if __name__ == "__mp_main__":
    install()
if __name__ == "__main__":
    import sys

    denied = sys.argv[1:] == ["--deny-placement"]
    assert sys.argv[1:] in ([], ["--deny-placement"], ["--reject-children"], ["--zero"])
    reject = sys.argv[1:] == ["--reject-children"]
    zero = sys.argv[1:] == ["--zero"]
    if denied:
        os.environ["MINEAGENT_PLACEMENT_PROBE_DENY"] = "1"
        try:
            phase7.prove_loop(2)
        except observer.SearchControlAbort as error:
            assert error.phase == "submission"
        else:
            raise AssertionError("Missing placement was acknowledged")
        assert not PLACEMENTS
    else:
        phase7.prove_loop(0 if zero else 2, reject=reject)
        assert len(PLACEMENTS) == (0 if zero else 2)
        assert len({item["worker_namespace_pid"] for item in PLACEMENTS}) == (
            0 if zero else 1
        )
        assert len({item["worker_start_ticks"] for item in PLACEMENTS}) == (
            0 if zero else 1
        )
    print(
        json.dumps(
            {
                "schema": "mineagent.worker-placement-probe.v1",
                "placement_count": len(PLACEMENTS),
                "denied": denied,
                "children_rejected": reject,
                "zero_mutations": zero,
                "namespace_start_identity_observed": True,
                "scope": "actual pinned spawn/API with explicit synthetic host; no production issuance, SQL, SO_PEERCRED or containment acceptance",
            },
            sort_keys=True,
        )
    )
