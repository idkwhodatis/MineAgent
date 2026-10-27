"""Closed-launch contract/transport doubles; no local Docker/listener execution."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_rdagent_controller_process import records

from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational import supervisor as module
from runtimes.rdagent_operational.trusted_report import report_bytes


class Reader:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    def read_frame(self, *, deadline):
        return next(self.frames, None)

    def close(self):
        self.closed = True


def test_report_stream_accepts_fragmented_exact_two_reports():
    first = report_bytes("started", 2, 123)
    last = report_bytes("quiescent", 2, 123, 0)
    reader = Reader([(1, first[:5]), (1, first[5:] + last[:8]), (1, last[8:])])
    stream = module.ReportStream(reader)
    assert stream.next(10)["event"] == "started"
    assert len(stream.finish(10)) == 2


@pytest.mark.parametrize(
    "frames",
    [
        [(2, b"error")],
        [(1, b"x" * 2049)],
        [(1, b"{")],
        [(1, report_bytes("quiescent", 2, 123, 0))],
        [(1, report_bytes("started", 2, 123) * 2)],
        [(1, report_bytes("started", 2, 123) + report_bytes("quiescent", 3, 123, 0))],
        [
            (
                1,
                report_bytes("started", 2, 123)
                + report_bytes("quiescent", 2, 123, 0)
                + b"\n",
            )
        ],
        [(1, report_bytes("started", 2, 123) + b' {"schema":null}\n')],
    ],
)
def test_report_stream_rejects_wrong_stream_duplicate_trailing_or_changed_child(frames):
    with pytest.raises((ValueError, KeyError)):
        module.ReportStream(Reader(frames)).finish(10)


def test_authority_is_private_single_use_and_readonly(tmp_path):
    intent = records(tmp_path)[0].intent
    with pytest.raises(ProcessError):
        module.SingleUseControllerAuthority(intent)
    authority = module._issue_committed_authority(intent)
    with pytest.raises(AttributeError):
        authority.intent = intent
    authority.consume()
    with pytest.raises(ProcessError):
        authority.consume()


@pytest.fixture
def contract(tmp_path):
    launch = records(tmp_path)[0]
    a = launch.intent.admission
    operation = tmp_path / "operation"
    inventory = {
        "base_ref": module.BASE_IMAGE,
        "image_id": a.image_id,
        "environment": ["PATH=/usr/bin"],
        "working_directory": "/tmp",
    }
    host = {
        "Privileged": False,
        "ReadonlyRootfs": True,
        "NetworkMode": "none",
        "PidMode": "",
        "IpcMode": "none",
        "UTSMode": "",
        "UsernsMode": "",
        "Memory": 2 * 1024**3,
        "MemorySwap": 2 * 1024**3,
        "PidsLimit": 64,
        "NanoCpus": 1_000_000_000,
        "AutoRemove": False,
        "ShmSize": 65536,
        "CapAdd": ["CAP_KILL", "CAP_SETUID", "CAP_SETGID"],
        "CapDrop": ["ALL"],
        "SecurityOpt": [
            "no-new-privileges",
            "seccomp=" + json.dumps(module.seccomp_policy()),
        ],
        "MaskedPaths": [
            *module.MASKED_PATHS,
            *(
                str(p)
                for p in Path("/sys/devices/system/cpu").glob(
                    "cpu[0-9]*/thermal_throttle"
                )
            ),
        ],
        "ReadonlyPaths": list(module.READONLY_PATHS),
        "Tmpfs": module.TMPFS.copy(),
        "LogConfig": {"Type": "none", "Config": {}},
        "Ulimits": [
            {"Name": "nofile", "Hard": 256, "Soft": 256},
            {"Name": "fsize", "Hard": 262144, "Soft": 262144},
        ],
    }
    document = {
        "Id": "d" * 64,
        "Image": a.image_id,
        "Name": "/" + launch.runtime_name,
        "HostConfig": host,
        "Config": {
            "Env": inventory["environment"],
            "WorkingDir": "/tmp",
            "User": "0:0",
            "Entrypoint": list(module.ENTRYPOINT),
            "Cmd": [str(int(launch.intent.not_after.timestamp() * 1000))],
            "Labels": module._labels(launch),
            "Tty": False,
            "OpenStdin": False,
        },
        "Mounts": [
            {
                "Source": str(operation / "input" / "bootstrap.json"),
                "Destination": "/input/bootstrap.json",
                "Type": "bind",
                "RW": False,
                "Propagation": "rprivate",
            },
            {
                "Source": str(operation / "ipc"),
                "Destination": "/ipc",
                "Type": "bind",
                "RW": False,
                "Propagation": "rprivate",
            },
        ],
    }
    return launch, operation, inventory, document


def test_exact_controller_container_contract(contract):
    launch, operation, inventory, document = contract
    module.verify_container(
        document, launch, operation, inventory, container_id="d" * 64
    )

    # Docker CLI UlimitOpt.GetList sorts names, regardless of CLI argument order.
    limits = document["HostConfig"]["Ulimits"]
    document["HostConfig"]["Ulimits"] = list(reversed(limits))
    module.verify_container(document, launch, operation, inventory)
    import copy

    bad_values = [
        None,
        {},
        limits[:1],
        limits + [limits[0]],
        [limits[0]] * 2,
        [None, limits[1]],
        [{}, limits[1]],
    ]
    for field, bad in (
        ("Soft", 0),
        ("Hard", -1),
        ("Soft", True),
        ("Soft", "256"),
        ("Soft", None),
        ("Name", []),
        ("Hard", 256.0),
        ("Name", "nproc"),
    ):
        changed = copy.deepcopy(limits)
        changed[0][field] = bad
        bad_values.append(changed)
    extra = copy.deepcopy(limits)
    extra[0]["Unknown"] = 1
    bad_values.append(extra)
    for bad in bad_values:
        document["HostConfig"]["Ulimits"] = bad
        with pytest.raises(ValueError, match="Controller ulimit drift"):
            module.verify_container(document, launch, operation, inventory)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "Memory", 0),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "PidsLimit", 0),
        ("HostConfig", "ShmSize", 1048576),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "Tmpfs", {}),
        ("HostConfig", "LogConfig", {"Type": "json-file", "Config": {}}),
        ("HostConfig", "Init", True),
        ("HostConfig", "Devices", [{}]),
        ("HostConfig", "DeviceRequests", [{}]),
        ("HostConfig", "Binds", ["/etc:/etc"]),
        ("HostConfig", "VolumesFrom", ["other"]),
        ("HostConfig", "Links", ["other"]),
        ("HostConfig", "Ulimits", []),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
        ("HostConfig", "MaskedPaths", []),
        ("Config", "Env", ["PATH=/evil"]),
        ("Config", "WorkingDir", "/"),
        ("Config", "User", "65533:65533"),
        ("Config", "Entrypoint", ["sh"]),
        ("Config", "Cmd", ["999"]),
        ("Config", "Labels", {}),
        ("Config", "Tty", True),
        ("Config", "OpenStdin", True),
        ("Config", "Volumes", {"/evil": {}}),
        ("Config", "Healthcheck", {"Test": ["CMD", "sh"]}),
    ],
)
def test_resource_mount_entrypoint_negative_matrix(contract, section, key, value):
    launch, operation, inventory, document = contract
    document[section][key] = value
    with pytest.raises(ValueError):
        module.verify_container(document, launch, operation, inventory)


@pytest.mark.parametrize(
    "change", ["writable", "extra", "ipcsource", "image", "container"]
)
def test_exact_mount_and_runtime_binding(contract, change):
    launch, operation, inventory, document = contract
    if change == "writable":
        document["Mounts"][1]["RW"] = True
    elif change == "extra":
        document["Mounts"].append(document["Mounts"][0].copy())
    elif change == "ipcsource":
        document["Mounts"][1]["Source"] = "/tmp/public"
    elif change == "image":
        document["Image"] = "sha256:" + "f" * 64
    else:
        document["Id"] = "f" * 64
    with pytest.raises(ValueError):
        module.verify_container(
            document, launch, operation, inventory, container_id="d" * 64
        )


def test_no_reader_kernel_exit_precedes_reader_close_and_daemon_query(
    tmp_path, monkeypatch
):
    launch, identity, _, _ = records(tmp_path)
    events = []

    class CLI:
        removed = False

        def _inspect(self, name):
            events.append("inspect")
            return (
                None
                if self.removed
                else {"State": {"Running": False, "Status": "exited"}}
            )

        def _command(self, args):
            assert args == ["rm", identity.container_id]
            events.append("rm")
            self.removed = True

    reader = SimpleNamespace(close=lambda: events.append("reader_close"))
    listener = SimpleNamespace(close=lambda: events.append("listener_close"))
    supervisor = SimpleNamespace(cli=CLI(), inventory={})
    handle = module.RunningController(
        supervisor, tmp_path, launch, identity, 100, reader, listener
    )
    states = iter([False, True])

    def gone(value):
        events.append("kernel_check")
        return next(states)

    monkeypatch.setattr(module, "_kernel_gone", gone)
    monkeypatch.setattr(module.time, "monotonic", lambda: 50)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        module, "verify_container", lambda *a, **k: events.append("verify")
    )
    from contextlib import nullcontext

    monkeypatch.setattr(module, "process_fence", lambda root: nullcontext())
    monkeypatch.setattr(
        module, "write_record", lambda *a: events.append("journal_stop")
    )
    monkeypatch.setattr(handle, "_cleanup", lambda: events.append("cleanup"))
    stop = handle.finish(
        no_reader=True, on_stopped=lambda stop: events.append("db_stop")
    )
    assert stop.child_sha256 is None and stop.child_kernel_identity_absent is None
    assert events.index("reader_close") > max(
        i for i, v in enumerate(events) if v == "kernel_check"
    )
    assert events.index("inspect") > events.index("reader_close")
    assert events.index("journal_stop") > events.index("rm")
    assert events[-2:] == ["cleanup", "db_stop"]


def test_static_start_order_no_arbitrary_handler_or_new_write_mounts():
    import inspect

    source = inspect.getsource(module.ControllerSupervisor.start)
    assert source.index('write_record(control / "launch.json"') < source.index(
        "self.cli._command(args)"
    )
    assert source.index("AttachStream.connect(") < source.index(
        'self.cli._command(["start", cid])'
    )
    assert '"--pull=never"' in source
    assert '"--privileged"' not in source and '"--entrypoint"' not in source
    assert "Thread(" not in source and "handler" not in source


def test_image_inventory_closes_base_env_and_labels():
    base = {
        "RepoDigests": ["python@" + module.BASE_IMAGE.split("@")[1]],
        "Config": {"Env": ["PATH=/usr/local/bin:/usr/bin", "LANG=C.UTF-8"]},
    }
    image = {
        "Id": "sha256:" + "a" * 64,
        "Config": {
            "Env": base["Config"]["Env"]
            + ["HOME=/tmp", "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"],
            "User": "0:0",
            "WorkingDir": "/tmp",
            "Entrypoint": list(module.ENTRYPOINT),
            "Cmd": None,
            "Labels": {"io.mineagent.managed": module.MANAGED},
        },
    }
    assert module.verify_image_inventory(base, image)["image_id"] == image["Id"]
    image["Config"]["Env"].append("API_KEY=not-allowed")
    with pytest.raises(ValueError):
        module.verify_image_inventory(base, image)


def test_actual_staging_writer_is_exclusive_readonly_and_exact(tmp_path):
    path = tmp_path / "bootstrap.json"
    module._write_input(path, b'{"fixed":true}', 0o444)
    assert path.read_bytes() == b'{"fixed":true}'
    assert path.stat().st_mode & 0o777 == 0o444
    with pytest.raises(FileExistsError):
        module._write_input(path, b"changed", 0o444)
    target = tmp_path / "link"
    target.symlink_to(path)
    with pytest.raises(FileExistsError):
        module._write_input(target, b"changed", 0o444)
    assert path.read_bytes() == b'{"fixed":true}'


def test_unknown_cleanup_inode_is_retained_not_deleted(tmp_path):
    launch, identity, _, _ = records(tmp_path)
    handle = module.RunningController(None, tmp_path, launch, identity, 100, None, None)
    ipc = handle.operation / "ipc"
    ipc.mkdir(parents=True)
    (ipc / "unexpected").write_bytes(b"retain")
    with pytest.raises(ProcessError, match="cleanup_inventory"):
        handle._cleanup()
    assert (ipc / "unexpected").read_bytes() == b"retain"


def test_normal_reader_missing_quiescent_is_error_after_positive_stop(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext

    launch, identity, _, _ = records(tmp_path)
    observed = []

    class CLI:
        removed = False

        def _inspect(self, name):
            return (
                None
                if self.removed
                else {"State": {"Running": False, "Status": "exited"}}
            )

        def _command(self, args):
            self.removed = True

    reader = Reader([(1, report_bytes("started", 2, 123))])
    handle = module.RunningController(
        SimpleNamespace(cli=CLI(), inventory={}),
        tmp_path,
        launch,
        identity,
        100,
        reader,
        SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(module, "_kernel_gone", lambda identity: True)
    monkeypatch.setattr(module, "verify_container", lambda *a, **k: None)
    monkeypatch.setattr(module, "process_fence", lambda root: nullcontext())
    monkeypatch.setattr(
        module, "write_record", lambda path, payload: observed.append(payload)
    )
    monkeypatch.setattr(handle, "_cleanup", lambda: None)
    with pytest.raises(ProcessError, match="incomplete_or_failed_reports"):
        handle.finish()
    assert handle.finished and observed[0]["confirmed_absent"] is True
    assert reader.closed


@pytest.fixture
def stop_handle(tmp_path, monkeypatch):
    from contextlib import nullcontext

    launch, identity, _, _ = records(tmp_path)
    events = []

    class CLI:
        removed = False

        def _inspect(self, name):
            events.append("inspect")
            return (
                None
                if self.removed
                else {"State": {"Running": False, "Status": "exited"}}
            )

        def _command(self, args):
            events.append("rm")
            self.removed = True

    handle = module.RunningController(
        SimpleNamespace(cli=CLI(), inventory={}),
        tmp_path,
        launch,
        identity,
        100,
        Reader([]),
        SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(module, "_kernel_gone", lambda identity: True)
    monkeypatch.setattr(module, "verify_container", lambda *a, **k: None)
    monkeypatch.setattr(module, "process_fence", lambda root: nullcontext())
    monkeypatch.setattr(
        module, "write_record", lambda path, payload: events.append("journal")
    )
    monkeypatch.setattr(handle, "_cleanup", lambda: events.append("cleanup"))
    return handle, events


def test_cleanup_failure_replay_never_repeats_docker_action(stop_handle, monkeypatch):
    handle, events = stop_handle
    failures = [True]

    def cleanup():
        events.append("cleanup")
        if failures.pop() if failures else False:
            raise OSError("interrupted cleanup")

    monkeypatch.setattr(handle, "_cleanup", cleanup)
    with pytest.raises(OSError, match="interrupted cleanup"):
        handle.finish(no_reader=True)
    assert handle._stop_record is not None and not handle.finished
    before = events.count("inspect")
    stop = handle.finish(no_reader=True)
    assert events.count("rm") == 1 and events.count("inspect") == before
    assert handle.finish() is stop
    assert events.count("rm") == 1


def test_callback_acknowledged_only_after_successful_fresh_callback(stop_handle):
    handle, events = stop_handle

    def failed(stop):
        raise ValueError("fence gone")

    with pytest.raises(ValueError, match="fence gone"):
        handle.finish(no_reader=True, on_stopped=failed)
    assert handle.finished and not handle._callback_acknowledged
    acknowledgements = []
    original = list(events)
    handle.finish(on_stopped=lambda stop: acknowledgements.append(stop))
    handle.finish(on_stopped=lambda stop: acknowledgements.append(stop))
    assert len(acknowledgements) == 1 and events == original


def test_concurrent_or_unresolved_finish_never_repeats_docker(stop_handle, monkeypatch):
    handle, events = stop_handle
    handle._stop_lock.acquire()
    try:
        with pytest.raises(ProcessError, match="stop_busy"):
            handle.finish(no_reader=True)
    finally:
        handle._stop_lock.release()
    monkeypatch.setattr(module, "_kernel_gone", lambda identity: False)
    monkeypatch.setattr(module.time, "monotonic", lambda: 102)
    with pytest.raises(ProcessError, match="kernel_deadline"):
        handle.finish(no_reader=True)
    with pytest.raises(ProcessError, match="requires_recovery"):
        handle.finish(no_reader=True)
    assert events == []


def test_seccomp_validation_precedes_listener_allocation():
    import inspect

    source = inspect.getsource(module.ControllerSupervisor.start)
    assert source.index("seccomp_policy()") < source.index("listener = socket.socket")


def test_cleanup_replay_accepts_only_remaining_fixed_files(tmp_path):
    launch, identity, _, _ = records(tmp_path)
    handle = module.RunningController(None, tmp_path, launch, identity, 100, None, None)
    directory = handle.operation / "input"
    directory.mkdir(parents=True)
    (directory / "bootstrap.json").write_bytes(b"retained fixture")
    handle._cleanup()
    handle._cleanup()
    assert not directory.exists()


@pytest.fixture
def launch_flow(tmp_path, monkeypatch):
    """Actual staging writes, simulated sockets/Docker/kernel; never a Docker proof."""
    import hashlib
    from contextlib import contextmanager

    launch, identity, _, _ = records(tmp_path)
    ticket, raw = "e" * 64, b"{}"
    admission = launch.intent.admission.model_copy(
        update={
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "ipc": launch.intent.admission.ipc.model_copy(
                update={"ticket_sha256": hashlib.sha256(ticket.encode()).hexdigest()}
            ),
        }
    )
    intent = launch.intent.model_copy(
        update={
            "admission": admission,
            "bootstrap_sha256": admission.input_sha256,
            "bootstrap_size_bytes": len(raw),
        }
    )
    authority = module._issue_committed_authority(intent)
    events, locked = [], [False]

    @contextmanager
    def fence(root):
        assert not locked[0]
        locked[0] = True
        events.append("lock")
        namespace = root / "research-processes"
        namespace.mkdir(exist_ok=True)
        try:
            yield namespace
        finally:
            locked[0] = False
            events.append("unlock")

    monkeypatch.setattr(module, "process_fence", fence)
    monkeypatch.setattr(module, "seccomp_policy", lambda: None)
    monkeypatch.setattr(module, "verify_container", lambda *a, **kw: None)
    monkeypatch.setattr(
        module,
        "DaemonIdentityContext",
        lambda *args: SimpleNamespace(observe=lambda pid: identity.leader),
    )
    real_binding = module._staged_binding
    monkeypatch.setattr(module, "_staged_binding", lambda *a: ("unchanged",))

    class Listener:
        closed = False

        def bind(self, path):
            Path(path).touch()

        def listen(self, count):
            assert count == 1

        def close(self):
            self.closed = True
            events.append("listener_close")

    listener = Listener()
    monkeypatch.setattr(module.socket, "socket", lambda *a: listener)
    reader = Reader([])

    def connect(*args, **kwargs):
        events.append("attach")
        return reader

    monkeypatch.setattr(module.AttachStream, "connect", connect)
    running = [False]

    def command(args):
        assert locked[0]
        events.append(args[0])
        if args[0] == "start":
            running[0] = True
        return 0, ("d" * 64).encode()

    supervisor = module.ControllerSupervisor(
        admission.image_id, {"image_id": admission.image_id}
    )
    supervisor.cli = SimpleNamespace(
        _command=command,
        _inspect=lambda cid: {
            "State": {"Running": running[0], "Pid": identity.leader.host_pid}
        },
    )

    def callback():
        assert not locked[0]
        events.append("authorize")

    def start(callback_override=None):
        return supervisor.start(
            tmp_path,
            authority,
            raw,
            ticket,
            on_identity=lambda _: events.append("identity"),
            before_submission=callback
            if callback_override is None
            else callback_override,
        )

    return SimpleNamespace(
        start=start,
        events=events,
        locked=locked,
        callback=callback,
        reader=reader,
        listener=listener,
        root=tmp_path,
        real_binding=real_binding,
        raw=raw,
        ticket=ticket,
    )


def test_fresh_authorization_outside_namespace_before_both_effects(launch_flow):
    flow = launch_flow
    handle = flow.start()
    assert [
        e
        for e in flow.events
        if e in {"authorize", "create", "attach", "start", "identity"}
    ] == ["authorize", "create", "attach", "authorize", "start", "identity"]
    assert not flow.locked[0]
    handle.listener.close()
    handle.reader.close()


@pytest.mark.parametrize("fail_at", [1, 2])
def test_stale_submission_keeps_discoverable_no_repeat_or_fake_stop(
    launch_flow, fail_at
):
    flow = launch_flow
    calls = []

    def stale():
        flow.callback()
        calls.append(1)
        if len(calls) == fail_at:
            raise ProcessError("stale_fence")

    with pytest.raises(ProcessError, match="stale_fence"):
        flow.start(stale)
    assert flow.events.count("create") == fail_at - 1
    assert "start" not in flow.events
    assert flow.listener.closed
    if fail_at == 2:
        assert flow.reader.closed
    assert len(list(flow.root.rglob("launch.json"))) == 1
    assert not list(flow.root.rglob("stop.json"))
    assert not flow.locked[0]


@pytest.mark.parametrize("change_at", [2, 3])
def test_binding_changed_while_fence_released_refuses_effect(
    launch_flow, monkeypatch, change_at
):
    calls = []

    def binding(*args):
        calls.append(1)
        return (len(calls) >= change_at,)

    monkeypatch.setattr(module, "_staged_binding", binding)
    with pytest.raises(ProcessError, match="staged_binding_changed"):
        launch_flow.start()
    assert launch_flow.events.count("create") == change_at - 2
    assert "start" not in launch_flow.events
    assert launch_flow.listener.closed


@pytest.mark.parametrize(
    "change", ["none", "bootstrap", "ticket", "journal", "extra", "inode", "mode"]
)
def test_exact_staged_file_binding(launch_flow, monkeypatch, change):
    import os
    import stat

    flow = launch_flow
    handle = flow.start()
    handle.listener.close()
    handle.reader.close()
    operation = module.operation_path(flow.root, handle.launch.intent.admission)
    (operation / "control/identity.json").unlink()
    original_stat = os.stat

    def socket_stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == "peer.sock":
            values = list(info)
            values[0] = stat.S_IFSOCK | 0o666
            return os.stat_result(values)
        return info

    # Listener creation is forbidden locally; only socket inode's type is doubled.
    # All root/directory/journal/bootstrap/ticket bytes and metadata are actual.
    monkeypatch.setattr(module.os, "stat", socket_stat)
    before = flow.real_binding(flow.root, handle.launch, flow.raw, flow.ticket)
    if change == "none":
        assert before == flow.real_binding(
            flow.root, handle.launch, flow.raw, flow.ticket
        )
        return
    target = operation / "input/bootstrap.json"
    if change == "bootstrap":
        target.chmod(0o600)
        target.write_bytes(b"[]")
        target.chmod(0o444)
    elif change == "ticket":
        target = operation / "ipc/ticket.json"
        target.chmod(0o600)
        target.write_bytes(b"{}")
        target.chmod(0o444)
    elif change == "journal":
        (operation / "control/launch.json").write_bytes(b"{}")
    elif change == "extra":
        (operation / "input/extra").touch()
    elif change == "mode":
        target.chmod(0o644)
    else:
        target.rename(operation / "old-input")
        target.write_bytes(flow.raw)
        target.chmod(0o444)
        (operation / "old-input").unlink()
        assert before != flow.real_binding(
            flow.root, handle.launch, flow.raw, flow.ticket
        )
        return
    with pytest.raises(ProcessError):
        flow.real_binding(flow.root, handle.launch, flow.raw, flow.ticket)


@pytest.mark.parametrize(
    "exit_code,oom,observed,successful",
    [
        (124, False, 100.2, True),
        (137, True, 100.2, False),
        (0, False, 100.2, False),
        (124, False, 99.0, False),
        (124, None, 100.2, False),
        (True, False, 100.2, False),
    ],
)
def test_actual_exit_and_original_deadline_required_after_positive_stop(
    stop_handle, monkeypatch, exit_code, oom, observed, successful
):
    handle, events = stop_handle
    original = handle.supervisor.cli._inspect

    def inspect(name):
        document = original(name)
        if document is not None:
            document["State"].update(ExitCode=exit_code, OOMKilled=oom)
        return document

    monkeypatch.setattr(handle.supervisor.cli, "_inspect", inspect)
    monkeypatch.setattr(module.time, "monotonic", lambda: observed)
    monkeypatch.setattr(
        handle.reports,
        "finish",
        lambda deadline: [
            {"event": "started"},
            {"event": "quiescent", "child_exit_code": 0},
        ],
    )
    stopped = []
    if successful:
        handle.finish(on_stopped=stopped.append)
    else:
        with pytest.raises(ProcessError, match="original_deadline_completion"):
            handle.finish(on_stopped=stopped.append)
    assert handle.finished and len(stopped) == 1
    assert stopped[0].confirmed_absent is True
    assert handle.original_deadline_completion is successful
    assert handle.measured_exit_code == (exit_code if type(exit_code) is int else None)
    assert events.count("rm") == 1
