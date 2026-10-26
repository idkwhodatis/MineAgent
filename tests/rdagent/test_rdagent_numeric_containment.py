"""Mocked observation guard tests; no candidate execution on the host."""

import ast
import copy
import hashlib
import itertools

import pytest

from runtimes.factor_execution import containment_observation as obs
from runtimes.factor_execution.containment_acceptance import (
    fixture_source,
    require_report,
)
from runtimes.factor_execution.containment_fixture import MARKER, MODES


@pytest.mark.parametrize("mode", MODES)
def test_closed_source_identity(mode):
    source = fixture_source(mode)
    assert ast.literal_eval(ast.parse(source).body[0].value) == mode
    assert b"import h5py" in source and b"import numpy" in source
    assert b"libc.ptrace(16, 1, 0, 0)" in source


@pytest.mark.parametrize("mode", [None, "", "foreign", 1, [], "';bad"])
def test_unknown_mode_rejected(mode):
    with pytest.raises(ValueError):
        fixture_source(mode)


def table():
    return "PID UID PPID PIDNS SID\n100 0 50 9 100\n101 65533 100 9 101\n102 65533 101 9 102\n103 65533 101 9 103\n104 65533 101 9 104"


@pytest.fixture
def observation(monkeypatch):
    times = itertools.count()
    monkeypatch.setattr(obs.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(obs.time, "sleep", lambda _: None)
    monkeypatch.setattr(obs, "remaining_command_budget", lambda _: 1)
    monkeypatch.setattr(obs, "process_identity", lambda pid: str(pid))
    monkeypatch.setattr(obs, "live_process", lambda pid, start: True)
    monkeypatch.setattr(obs, "command", lambda *args, **kwargs: table())
    return monkeypatch


def test_exact_four_live_private_detached_identities(observation):
    assert obs.observe_tree("fixed", 100, 8, 9) == {
        pid: str(pid) for pid in range(101, 105)
    }


@pytest.mark.parametrize(
    "old,new",
    [
        ("100 0", "100 65533"),
        ("101 65533 100 9 101", "101 65533 100 7 101"),
        ("104 65533 101 9 104", "104 65533 100 9 104"),
        ("104 65533 101 9 104", "104 65533 900 9 104"),
        ("104 65533 101 9 104", "103 65533 101 9 104"),
        ("104 65533 101 9 104", "104 65533 101 9 101"),
        ("104 65533 101 9 104", "104 65533 101"),
        ("104 65533 101 9 104", "104 65533 101 x 104"),
    ],
)
def test_wrong_or_undetached_tree_fails(observation, old, new):
    observation.setattr(
        obs, "command", lambda *args, **kwargs: table().replace(old, new)
    )
    with pytest.raises(AssertionError):
        obs.observe_tree("fixed", 100, 8, 9)


@pytest.mark.parametrize("missing", ["identity", "live"])
def test_dead_or_unpinned_descendants_fail(observation, missing):
    if missing == "identity":
        observation.setattr(obs, "process_identity", lambda pid: None)
    else:
        observation.setattr(obs, "live_process", lambda pid, start: False)
    with pytest.raises(AssertionError):
        obs.observe_tree("fixed", 100, 8, 9)


def test_exporter_twice_uses_exact_shared_kernel_validator(observation):
    rows = "PID UID PPID PIDNS COMM\n100 0 50 9 python\n105 0 100 9 ma-export-v2"
    observation.setattr(obs, "command", lambda *args, **kwargs: rows)
    calls = []
    observation.setattr(
        obs,
        "require_exporter_observation",
        lambda rows, leader: calls.append((rows, leader)) or (105, "start"),
    )
    assert obs.observe_exporter("fixed", 100, 8, 9) == {105: "start"}
    assert len(calls) == 2 and calls[0] == calls[1]


def test_changed_exporter_identity_fails(observation):
    observation.setattr(
        obs, "command", lambda *args, **kwargs: "HEADER\n105 0 100 9 ma-export-v2"
    )
    answers = iter([(105, "before"), (105, "after")])
    observation.setattr(
        obs, "require_exporter_observation", lambda rows, leader: next(answers)
    )
    with pytest.raises(AssertionError):
        obs.observe_exporter("fixed", 100, 8, 9)


def proof():
    cases = []
    for mode in MODES:
        frame = mode == "tree_quiescence"
        unit = {
            key: True
            for key in (
                "positive_stop_verified",
                "original_deadline_verified",
                "launch_owner_killed",
                "all_observed_descendants_stopped",
                "host_sibling_survived",
            )
        }
        unit.update(
            role="candidate",
            observation_mode=mode,
            exit_code=124,
            elapsed_seconds=8.1,
            four_live_candidate_processes_observed=mode.startswith("tree_"),
            live_protected_exporter_observed_twice=mode
            in ("no_reader", "collector_disconnected"),
            kernel_exit_before_reader_close_verified=mode == "no_reader",
            primary_collector_disconnected=mode == "collector_disconnected",
            complete_output_stream_verified=mode.startswith("tree_"),
            protected_quiescence_frame_verified=frame,
            frame_received_while_unit_alive=frame,
            observed_child_exit_code=0 if frame else None,
            output_bytes=len(MARKER) if frame else 0,
            output_sha256=hashlib.sha256(MARKER if frame else b"").hexdigest(),
        )
        cases.append(unit)
    return {
        "scope": "disabled_maintained_numeric_containment_only",
        "arbitrary_candidate_admission": False,
        "cases": cases,
    }


def test_actual_observation_matrix_is_closed():
    require_report(proof())


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "four_live_candidate_processes_observed", False),
        (0, "output_sha256", "a" * 64),
        (0, "observed_child_exit_code", -11),
        (0, "frame_received_while_unit_alive", False),
        (1, "output_bytes", 100),
        (1, "observed_child_exit_code", 0),
        (2, "live_protected_exporter_observed_twice", False),
        (2, "kernel_exit_before_reader_close_verified", False),
        (2, "complete_output_stream_verified", True),
        (2, "elapsed_seconds", 10),
        (3, "primary_collector_disconnected", False),
        (3, "host_sibling_survived", False),
        (3, "exit_code", 0),
        (3, "all_observed_descendants_stopped", False),
    ],
)
def test_missing_evidence_cannot_pass(index, field, value):
    data = copy.deepcopy(proof())
    data["cases"][index][field] = value
    with pytest.raises(ValueError):
        require_report(data)


@pytest.mark.parametrize("mode", MODES)
def test_driver_orders_actual_kernel_exit_and_reader_close(monkeypatch, tmp_path, mode):
    """Control-flow guard only; fake daemon/kernel does NOT establish containment."""
    import json
    import os
    import struct
    from pathlib import Path

    from runtimes.factor_execution import acceptance as driver

    events, stopped = [], [False]
    leader = os.getpid()

    class Process:
        def __init__(self, args):
            self.dead = False
            if args[0] == "python":
                Path(args[-1]).write_text("started")

        def kill(self):
            self.dead = True

        def wait(self, timeout):
            return 0

        def poll(self):
            return -9 if self.dead else None

    class Reader:
        def close(self):
            events.append("close")

        def collect_until_eof(self, deadline):
            events.append("eof")
            return {1: b"", 2: b""}

    reader = Reader()
    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver.AttachStream, "connect", lambda *args, **kwargs: reader)

    def command(*args, **kwargs):
        if args[1] == "create":
            return "a" * 64
        return "PID UID\n" + str(leader) + (" 0" if args[-1] == "pid,uid" else "")

    monkeypatch.setattr(driver, "command", command)
    monkeypatch.setattr(
        driver,
        "inspect",
        lambda *args, **kwargs: {
            "State": {"Pid": leader, "Running": not stopped[0], "ExitCode": 124}
        },
    )
    monkeypatch.setattr(driver, "verify_inspect", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "require_process_observation", lambda *args: None)
    monkeypatch.setattr(
        driver, "process_identity", lambda pid: None if stopped[0] else "old"
    )
    monkeypatch.setattr(obs, "observe_tree", lambda *args: {leader + 1: "old"})
    monkeypatch.setattr(obs, "observe_exporter", lambda *args: {leader + 1: "old"})

    def exited(*args):
        events.append("kernel")
        stopped[0] = True

    monkeypatch.setattr(driver, "wait_kernel_exit", exited)
    metadata = json.dumps(
        {
            "schema": "mineagent.numeric-quiescence.v2",
            "basename": "result.h5",
            "child_exit_code": 0,
            "descendants_reaped": True,
            "size": len(MARKER),
            "sha256": hashlib.sha256(MARKER).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    monkeypatch.setattr(
        driver,
        "read_numeric_frame",
        lambda *args: (2, struct.pack(">I", len(metadata)) + metadata + MARKER),
    )
    _, report = driver.exercise(
        "sha256:" + "a" * 64,
        tmp_path / "case",
        role="candidate",
        inventory={},
        input_bytes={},
        observation_mode=mode,
    )
    assert report["kernel_exit_before_reader_close_verified"] is (mode == "no_reader")
    assert report["complete_output_stream_verified"] is mode.startswith("tree_")
    if mode == "no_reader":
        assert events.index("kernel") < events.index("close")
    if mode == "collector_disconnected":
        assert events.index("close") < events.index("kernel")
    if mode.startswith("tree_"):
        assert events.index("eof") < events.index("kernel")
