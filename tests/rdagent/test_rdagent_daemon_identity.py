"""Daemon/proc authority doubles; no claim of actual Docker observation."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational import daemon_identity as module

RAW = b"PID UID PPID PIDNS COMMAND\n100 0 1 4026532777 python\n101 65533 100 4026532777 python\n"


def test_strict_top_real_namespace_value():
    _diagnostic_preserves_parser_and_bounds_output()
    assert module.parse_top(RAW) == {
        100: (0, 1, 4026532777),
        101: (65533, 100, 4026532777),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 16385,
        b"PID UID PPID PIDNS COMMAND\n",
        RAW + RAW.splitlines()[1] + b"\n",
        RAW.replace(b"PIDNS", b"NS"),
        RAW.replace(b"4026532777", b"0"),
        RAW.replace(b"4026532777", b"-"),
        RAW.replace(b"65533", b"root"),
        RAW.replace(b"101 65533", b"-101 65533"),
        RAW.replace(b"python", b"two words"),
        RAW.replace(b"python", b"\xff"),
        RAW + b"\n",
        RAW.replace(b"python", b"x" * 65),
        b"PID UID PPID PIDNS COMMAND\n" + b"100 0 1 42 x\n" * 65,
    ],
)
def test_top_invalid_or_unbounded(raw):
    with pytest.raises(ProcessError):
        module.parse_top(raw)


@pytest.fixture
def observer(monkeypatch):
    cid, image = "d" * 64, "sha256:" + "b" * 64
    document = {
        "Id": cid,
        "Image": image,
        "State": {"Running": True, "Status": "running", "Pid": 100},
    }
    cli = SimpleNamespace(
        _inspect=Mock(return_value=document), _command=Mock(return_value=(0, RAW))
    )
    monkeypatch.setattr(module, "DockerSupervisor", lambda _: cli)
    hosts = {
        100: (100, 500, "0::/unit\n", 1, (0,) * 4, (0,) * 4),
        101: (101, 501, "0::/unit\n", 100, (65533,) * 4, (65533,) * 4),
    }
    host = Mock(side_effect=lambda pid: hosts[pid])
    monkeypatch.setattr(module, "_host_snapshot", host)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    return SimpleNamespace(
        value=module.DockerKernelObserver(cid, image, 100, 60),
        cli=cli,
        host=host,
        hosts=hosts,
        document=document,
    )


def test_observe_pair_real_inode_semantics_and_fixed_argv(observer):
    leader, child = observer.value.observe_pair(101)
    assert leader.pid_namespace == child.pid_namespace == "pid:[4026532777]"
    assert child.ppid == leader.host_pid and child.start_ticks == 501
    observer.cli._command.assert_called_once_with(
        ["top", "d" * 64, "-eo", "pid,uid,ppid,pidns,comm"]
    )
    assert observer.cli._inspect.call_count == 2
    assert observer.host.call_count == 4
    assert observer.value.observe(100) == leader


@pytest.mark.parametrize("change", ["cid", "image", "leader", "running", "status"])
def test_changed_unit_rejected(observer, change):
    if change == "cid":
        observer.document["Id"] = "e" * 64
    elif change == "image":
        observer.document["Image"] = "sha256:" + "e" * 64
    elif change == "leader":
        observer.document["State"]["Pid"] = 102
    elif change == "running":
        observer.document["State"]["Running"] = False
    else:
        observer.document["State"]["Status"] = "restarting"
    with pytest.raises(ProcessError):
        observer.value.observe(101)
    observer.cli._command.assert_not_called()


def test_changed_after_daemon_snapshot_rejected(observer):
    observer.cli._inspect.side_effect = [
        observer.document,
        {**observer.document, "Id": "e" * 64},
    ]
    with pytest.raises(ProcessError):
        observer.value.observe(101)


def test_pid_reuse_or_host_metadata_drift_rejected(observer):
    old = observer.hosts[101]
    observer.host.side_effect = [
        observer.hosts[100],
        old,
        observer.hosts[100],
        (101, 999, *old[2:]),
    ]
    with pytest.raises(ProcessError):
        observer.value.observe(101)


@pytest.mark.parametrize(
    "raw",
    [
        RAW.replace(b"101 65533", b"102 65533"),
        RAW.replace(b"101 65533", b"101 0"),
        RAW.replace(b"65533 100", b"65533 99"),
    ],
)
def test_daemon_membership_uid_parent_crosschecked(observer, raw):
    observer.cli._command.return_value = (0, raw)
    with pytest.raises(ProcessError):
        observer.value.observe(101)


def test_deadline_after_slow_command_rejected(observer, monkeypatch):
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[10, 11, 61]))
    with pytest.raises(ProcessError):
        observer.value.observe(101)


def test_actual_self_proc_snapshot_reads_only_public_metadata():
    import os

    value = module._host_snapshot(os.getpid())
    assert value[0] == os.getpid() and value[1] > 0 and value[4][0] == os.getuid()


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "name"),
        ("leader_pid", True),
        ("pid", False),
        ("deadline", float("inf")),
        ("image_id", "image:latest"),
    ],
)
def test_context_exact_types_before_any_daemon(observer, field, value):
    args = {
        "container_id": "d" * 64,
        "image_id": "sha256:" + "b" * 64,
        "leader_pid": 100,
        "pid": 101,
        "deadline": 60,
    }
    args[field] = value
    with pytest.raises(ProcessError):
        module.observe_identity(**args)
    observer.cli._inspect.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "short"),
        ("image_id", "unfixed:tag"),
        ("leader_pid", True),
        ("leader_pid", 0),
        ("deadline", True),
        ("deadline", -1),
        ("deadline", float("nan")),
    ],
)
def test_context_constructor_strict(field, value):
    args = {
        "container_id": "d" * 64,
        "image_id": "sha256:" + "b" * 64,
        "leader_pid": 100,
        "deadline": 60.0,
    }
    args[field] = value
    with pytest.raises(ProcessError):
        module.DaemonIdentityContext(**args)


def test_context_is_frozen_observation_data_not_peer_authority(observer):
    from dataclasses import FrozenInstanceError

    assert type(observer.value) is module.DaemonIdentityContext
    with pytest.raises(FrozenInstanceError):
        observer.value.container_id = "f" * 64


@pytest.mark.parametrize(
    "profile,seconds",
    [
        ("mineagent.rdagent-orchestrator.v2", 60),
        ("mineagent.rdagent-orchestrator.v3", 120),
    ],
)
def test_profile_bound_observation_uses_original_deadline(
    observer, monkeypatch, profile, seconds
):
    context = module.DaemonIdentityContext(
        observer.value.container_id,
        observer.value.image_id,
        100,
        10.0 + seconds,
        profile,
    )
    assert context.observe_pair(101)[1].host_pid == 101
    # Expiry of the same absolute monotonic deadline never grants a fresh window.
    monkeypatch.setattr(module.time, "monotonic", lambda: 11.0 + seconds)
    with pytest.raises(ProcessError):
        context.observe_pair(101)


@pytest.mark.parametrize(
    "profile,seconds",
    [
        ("mineagent.rdagent-orchestrator.v2", 61),
        ("mineagent.rdagent-orchestrator.v3", 121),
    ],
)
def test_profile_excess_rejected_before_daemon_read(observer, profile, seconds):
    context = module.DaemonIdentityContext(
        observer.value.container_id,
        observer.value.image_id,
        100,
        10.0 + seconds,
        profile,
    )
    with pytest.raises(ProcessError):
        context.observe_pair(101)
    observer.cli._inspect.assert_not_called()


def _diagnostic_preserves_parser_and_bounds_output():
    import contextlib
    import io
    import json

    from tests.integration.rd_daemon_top_diagnostic import (
        diagnose_daemon_top,
        top_shape,
    )

    original = module.parse_top
    output = io.StringIO()
    private = b"private-command-token"
    with contextlib.redirect_stdout(output), diagnose_daemon_top():
        assert module.parse_top(RAW) == original(RAW)
        for _ in range(12):
            with pytest.raises(ProcessError, match="^rd_controller_daemon_identity$"):
                module.parse_top(
                    RAW.replace(b"4026532777", b"-").replace(b"python", private)
                )
    assert module.parse_top is original
    text = output.getvalue()
    assert (
        "100" not in text and "4026532777" not in text and private.decode() not in text
    )
    records = json.loads(text.split(":", 1)[1])
    assert (
        records == [{"category": "numeric_field", "field": "pidns", "kind": "dash"}] * 8
    )
    assert top_shape(RAW.replace(b"python", b"two words")) == {
        "category": "row_width",
        "width": "long",
    }
    assert top_shape(RAW.replace(b"65533", private)) == {
        "category": "numeric_field",
        "field": "uid",
        "kind": "other",
    }
    assert top_shape(b"x" * 16385) == {"category": "type_or_size"}
    with contextlib.redirect_stdout(io.StringIO()) as success, diagnose_daemon_top():
        module.parse_top(RAW)
    assert success.getvalue() == ""

    # Force all concurrent calls past projection entry before any records append.
    # The diagnostic remains bounded while each original ProcessError propagates.
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from tests.integration import rd_daemon_top_diagnostic as diagnostic

    barrier = threading.Barrier(12)
    shape = diagnostic.top_shape

    def simultaneous(raw):
        barrier.wait(timeout=5)
        return shape(raw)

    output = io.StringIO()
    with (
        contextlib.redirect_stdout(output),
        patch.object(diagnostic, "top_shape", simultaneous),
        diagnose_daemon_top(),
        ThreadPoolExecutor(max_workers=12) as pool,
    ):
        futures = [
            pool.submit(module.parse_top, RAW.replace(b"4026532777", b"-"))
            for _ in range(12)
        ]
        for future in futures:
            with pytest.raises(ProcessError, match="^rd_controller_daemon_identity$"):
                future.result()
    assert module.parse_top is original
    assert len(json.loads(output.getvalue().split(":", 1)[1])) == 8
