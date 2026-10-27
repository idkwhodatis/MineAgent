"""Separate phase8 observation/wait contracts; no kernel-authority acceptance."""

import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from packages.openevolve_adapter import observer, placement_observer
from runtimes.openevolve_phase8 import apply as patcher
from runtimes.openevolve_phase8.placement_probe import await_expected


def test_phase8_patch_hash_and_single_postsubmit_seam():
    manifest = json.loads((patcher.ROOT / "manifest.json").read_bytes())
    raw = (patcher.ROOT / manifest["patch"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["patch_sha256"]
    assert patcher.expected_inventory()[manifest["file"]] == manifest["before_sha256"]
    added = [
        line
        for line in raw.decode().splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert sum("observe_submitted(self.executor" in line for line in added) == 1
    assert not any("sample_from_island(" in line or "random." in line for line in added)


def test_placement_has_no_default_success_transport():
    with pytest.raises(observer.SearchControlAbort):
        placement_observer.send_placement({})


@pytest.mark.parametrize(
    "fault", ["many_workers", "dead_worker", "wrong_ack", "bool_start"]
)
def test_placement_failure_poison_epoch(fault):
    epoch = uuid4()
    descriptor = {"epoch_id": str(epoch), "intent_id": str(uuid4())}
    process = SimpleNamespace(
        pid=os.getpid(), is_alive=lambda: fault != "dead_worker", exitcode=None
    )
    executor = SimpleNamespace(
        _max_workers=2 if fault == "many_workers" else 1,
        _processes={os.getpid(): process},
    )

    def reply(payload):
        return {
            "placement_receipt_id": str(uuid4()),
            "epoch_id": str(epoch),
            "intent_id": descriptor["intent_id"],
            "worker_namespace_pid": os.getpid() + 1
            if fault == "wrong_ack"
            else os.getpid(),
            "worker_start_ticks": True
            if fault == "bool_start"
            else payload["worker_start_ticks"],
        }

    with (
        observer.epoch_control(epoch),
        patch.object(placement_observer, "send_placement", side_effect=reply),
    ):
        with pytest.raises(observer.SearchControlAbort):
            placement_observer.observe_submitted(executor, descriptor)
        with pytest.raises(observer.SearchControlAbort):
            observer.require_trusted_population()


def test_missing_expected_placement_wait_is_bounded_without_sql(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEAGENT_EXPLICIT_LOOP_PROBE", str(tmp_path))
    start = time.monotonic()
    with pytest.raises(observer.SearchControlAbort):
        await_expected({"intent_id": str(uuid4())}, seconds=0.025)
    assert 0.02 <= time.monotonic() - start < 0.5


def test_observed_start_ticks_come_from_controller_proc_read():
    epoch = uuid4()
    descriptor = {"epoch_id": str(epoch), "intent_id": str(uuid4())}
    process = SimpleNamespace(pid=os.getpid(), is_alive=lambda: True, exitcode=None)
    executor = SimpleNamespace(_max_workers=1, _processes={os.getpid(): process})
    seen = []

    def reply(payload):
        seen.append(payload)
        return {
            **{
                key: payload[key]
                for key in (
                    "epoch_id",
                    "intent_id",
                    "worker_namespace_pid",
                    "worker_start_ticks",
                )
            },
            "placement_receipt_id": str(uuid4()),
        }

    with (
        observer.epoch_control(epoch),
        patch.object(placement_observer, "send_placement", side_effect=reply),
    ):
        placement_observer.observe_submitted(executor, descriptor)
    actual = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19])
    assert seen[0]["worker_start_ticks"] == actual


@pytest.mark.parametrize("fault", ["extra", "changed", "symlink", "repeat"])
def test_phase8_rejects_unexpected_inventory_without_partial_write(
    tmp_path, monkeypatch, fault
):
    import difflib

    root, source = tmp_path / "control", tmp_path / "source"
    root.mkdir()
    (source / "openevolve").mkdir(parents=True)
    name = "openevolve/process_parallel.py"
    before, after = b"before\n", b"after\n"
    (source / name).write_bytes(before)
    patch = "".join(
        difflib.unified_diff(
            before.decode().splitlines(True),
            after.decode().splitlines(True),
            fromfile="a/" + name,
            tofile="b/" + name,
        )
    ).encode()
    manifest = {
        "schema": "mineagent.openevolve-placement-patch.v1",
        "upstream_commit": "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc",
        "file": name,
        "patch": "0008-observe-submitted-worker-placement.patch",
        "patch_sha256": patcher.sha(patch),
        "before_sha256": patcher.sha(before),
        "after_sha256": patcher.sha(after),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / manifest["patch"]).write_bytes(patch)
    monkeypatch.setattr(patcher, "ROOT", root)
    monkeypatch.setattr(
        patcher, "expected_inventory", lambda: {name: patcher.sha(before)}
    )
    if fault == "extra":
        (source / "openevolve/extra.py").write_text("x")
    elif fault == "changed":
        (source / name).write_bytes(b"drift\n")
    elif fault == "symlink":
        (source / "openevolve/linked").symlink_to(source / name)
    else:
        patcher.apply(source)
    previous = (source / name).read_bytes()
    with pytest.raises(ValueError):
        patcher.apply(source)
    assert (source / name).read_bytes() == previous


@pytest.mark.parametrize("fault", ["bool", "same_pid", "overflow", "unknown"])
def test_placement_dto_rejects_ambiguous_kernel_identity(fault):
    from packages.openevolve_adapter.placement_contracts import (
        WorkerPlacementObservation,
    )

    data = {
        "schema_version": "mineagent.openevolve-worker-placement.v1",
        "epoch_id": str(uuid4()),
        "intent_id": str(uuid4()),
        "controller_namespace_pid": 1,
        "worker_namespace_pid": 2,
        "worker_start_ticks": 100,
    }
    if fault == "bool":
        data["worker_namespace_pid"] = True
    elif fault == "same_pid":
        data["worker_namespace_pid"] = 1
    elif fault == "overflow":
        data["worker_start_ticks"] = 9223372036854775808
    else:
        data["host_pid"] = 99
    with pytest.raises(ValueError):
        WorkerPlacementObservation.model_validate_json(json.dumps(data))
