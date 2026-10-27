"""Closed protected OE-v1 launcher; no public registration or DB authority.

Trusted issuer must commit intent and independently recheck the current fenced
admission before minting one-use authority. Caller retains shared_lifecycle for
this entire handle, including callbacks/stop/cleanup. CLI timeout is uncertain,
never evidence of nonexecution. Unknown creation leaves its durable named launch
journal for containment recovery; never retry create from that journal.
"""

import hashlib
import json
import os
import re
import socket
import stat
import struct
import threading
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.factor_execution.attach_stream import AttachStream
from runtimes.factor_execution.runtime_contract import (
    BASE_IMAGE,
    MASKED_PATHS,
    READONLY_PATHS,
    bootstrap_capabilities,
    seccomp_policy,
)
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.openevolve_operational.peer import ControllerBinding
from runtimes.openevolve_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    ControllerStop,
)
from runtimes.openevolve_operational.trusted_report import (
    MAX_STREAM_BYTES,
    report_bytes,
)
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import (
    DockerSupervisor,
    _directory,
    _mkdir,
    operation_path,
    process_fence,
    write_record,
)

MANAGED = "mineagent-openevolve-orchestrator-v1"
ENTRYPOINT = ("python", "-I", "-m", "runtimes.openevolve_operational.deadline")
TMPFS = {
    "/tmp": "rw,noexec,nosuid,nodev,size=16m,uid=65533,gid=65533,mode=0700",
    "/work": "rw,noexec,nosuid,nodev,size=64m,uid=65533,gid=65533,mode=0700",
}
_ISSUER = object()


class SingleUseControllerAuthority:
    def __init__(self, intent, *, _issuer=None):
        if _issuer is not _ISSUER:
            raise ProcessError("oe_controller_launch_authority")
        self._intent = ControllerIntent.model_validate(intent)
        remaining = (self.intent.not_after - datetime.now(UTC)).total_seconds()
        if not 0 < remaining <= 600:
            raise ProcessError("oe_controller_deadline")
        self._deadline = time.monotonic() + remaining
        self._used = False
        self._lock = threading.Lock()
        self._issuer = _issuer

    @property
    def intent(self):
        return self._intent

    @property
    def deadline(self):
        return self._deadline

    def consume(self):
        with self._lock:
            if (
                self._issuer is not _ISSUER
                or self._used
                or time.monotonic() >= self.deadline
            ):
                raise ProcessError("oe_controller_launch_authority")
            self._used = True


def _issue_committed_authority(intent):
    """Trusted DB issuer only, after commit plus fresh fenced verification."""
    return SingleUseControllerAuthority(intent, _issuer=_ISSUER)


def _labels(launch):
    a = launch.intent.admission
    return {
        "io.mineagent.managed": MANAGED,
        "io.mineagent.admission": str(a.id),
        "io.mineagent.launch-sha256": digest(launch.model_dump(mode="json")),
    }


def verify_image_inventory(base, image):
    if not any(
        value.endswith("@" + BASE_IMAGE.split("@")[1])
        for value in base.get("RepoDigests", [])
    ):
        raise ValueError("Pinned Python base inventory required")
    config = image["Config"]
    expected = dict(entry.split("=", 1) for entry in base["Config"]["Env"])
    expected.update(HOME="/tmp", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    env = config.get("Env")
    if (
        not isinstance(env, list)
        or len(env) != len(expected)
        or dict(entry.split("=", 1) for entry in env) != expected
        or config.get("WorkingDir") != "/tmp"
        or config.get("User") != "0:0"
        or config.get("Entrypoint") != list(ENTRYPOINT)
        or config.get("Cmd") not in (None, [])
        or config.get("Volumes")
        or config.get("Healthcheck")
        or config.get("Labels") != {"io.mineagent.managed": MANAGED}
    ):
        raise ValueError("Exact controller image configuration required")
    return {
        "base_ref": BASE_IMAGE,
        "image_id": image["Id"],
        "environment": list(env),
        "working_directory": "/tmp",
    }


def verify_container(document, launch, operation, inventory, *, container_id=None):
    a = launch.intent.admission
    host, config = document["HostConfig"], document["Config"]
    expected = {
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
    }
    if any(host.get(k) != v for k, v in expected.items()):
        raise ValueError("Controller namespace/resource drift")
    bootstrap_capabilities(host.get("CapAdd"))
    if host.get("CapDrop") != ["ALL"]:
        raise ValueError("Controller capabilities drift")
    opts = host.get("SecurityOpt") or []
    policy = [v.removeprefix("seccomp=") for v in opts if v.startswith("seccomp=")]
    if (
        len(opts) != 2
        or "no-new-privileges" not in opts
        or len(policy) != 1
        or json.loads(policy[0]) != seccomp_policy()
    ):
        raise ValueError("Controller security profile drift")
    masked = sorted(
        [
            *MASKED_PATHS,
            *(
                str(p)
                for p in Path("/sys/devices/system/cpu").glob(
                    "cpu[0-9]*/thermal_throttle"
                )
            ),
        ]
    )
    if sorted(host.get("MaskedPaths") or []) != masked or sorted(
        host.get("ReadonlyPaths") or []
    ) != sorted(READONLY_PATHS):
        raise ValueError("Controller proc/sys protections drift")
    if (
        host.get("Tmpfs") != TMPFS
        or host.get("LogConfig") != {"Type": "none", "Config": {}}
        or host.get("Init") not in (None, False)
        or host.get("Devices")
        or host.get("DeviceRequests")
        or host.get("Binds")
        or host.get("VolumesFrom")
        or host.get("Links")
        or host.get("Ulimits")
        != [
            {"Name": "nofile", "Hard": 256, "Soft": 256},
            {"Name": "fsize", "Hard": 8388608, "Soft": 8388608},
        ]
    ):
        raise ValueError("Controller implicit mount/process/limit drift")
    if (
        inventory["base_ref"] != BASE_IMAGE
        or inventory["image_id"] != a.image_id
        or document.get("Image") != a.image_id
        or document.get("Name") != "/" + launch.runtime_name
        or container_id is not None
        and document.get("Id") != container_id
        or config.get("Env") != inventory["environment"]
        or config.get("WorkingDir") != "/tmp"
        or config.get("User") != "0:0"
        or config.get("Entrypoint") != list(ENTRYPOINT)
        or config.get("Cmd") != [str(int(launch.intent.not_after.timestamp() * 1000))]
        or config.get("Labels") != _labels(launch)
        or config.get("Tty") is not False
        or config.get("OpenStdin") is not False
        or config.get("Volumes")
        or config.get("Healthcheck")
    ):
        raise ValueError("Controller exact runtime/image identity drift")
    mounts = document.get("Mounts", [])
    expected_mounts = {
        (str(operation / "input"), "/bootstrap"),
        (str(operation / "ipc"), "/ipc"),
    }
    if (
        len(mounts) != 2
        or {(m.get("Source"), m.get("Destination")) for m in mounts} != expected_mounts
        or any(
            m.get("Type") != "bind"
            or m.get("RW") is not False
            or m.get("Propagation") != "rprivate"
            for m in mounts
        )
    ):
        raise ValueError("Controller closed readonly mount inventory drift")


class ReportStream:
    def __init__(self, reader):
        self.reader = reader
        self.pending = bytearray()
        self.total = 0
        self.events = []

    def next(self, deadline):
        while b"\n" not in self.pending:
            frame = self.reader.read_frame(deadline=deadline)
            if frame is None:
                if self.pending:
                    raise ValueError("Truncated controller report")
                return None
            stream, payload = frame
            self.total += len(payload)
            if stream != 1 or self.total > MAX_STREAM_BYTES:
                raise ValueError("Controller report channel bound/stream differs")
            self.pending.extend(payload)
        line, _, rest = self.pending.partition(b"\n")
        self.pending = bytearray(rest)
        if len(self.events) >= 2 or len(line) + 1 > 1024:
            raise ValueError("Duplicate/oversize controller report")
        value = json.loads(line)
        event = "started" if not self.events else "quiescent"
        expected = report_bytes(
            event,
            value["child_namespace_pid"],
            value["child_start_ticks"],
            value.get("child_exit_code"),
        )
        if line + b"\n" != expected:
            raise ValueError("Controller report shape/order differs")
        if self.events and any(
            value[k] != self.events[0][k]
            for k in ("child_namespace_pid", "child_start_ticks")
        ):
            raise ValueError("Controller report child identity changed")
        self.events.append(value)
        return value

    def finish(self, deadline):
        while self.next(deadline) is not None:
            pass
        return tuple(self.events)


def _write_input(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    parent = _directory(path.parent)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _staged_binding(root, launch, bootstrap_bytes, seed_bytes, ticket):
    """Exact immutable staging snapshot, while holding namespace process_fence."""
    operation = operation_path(root, launch.intent.admission)
    directories = (
        root,
        root / "research-processes",
        operation.parent,
        operation,
        operation / "control",
        operation / "input",
        operation / "ipc",
    )
    result = []
    for directory in directories:
        fd = _directory(directory)
        try:
            info = os.fstat(fd)
            result.append(
                (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
            )
            if directory == root and (info.st_dev, info.st_ino) != (
                launch.root_device,
                launch.root_inode,
            ):
                raise ProcessError("oe_controller_staged_root_changed")
            expected = {
                operation: {"control", "input", "ipc"},
                operation / "control": {"launch.json"},
                operation / "input": {"bootstrap.json", "seed.py"},
                operation / "ipc": {"ticket.json", "events.sock", "broker.sock"},
            }.get(directory)
            if expected is not None and set(os.listdir(fd)) != expected:
                raise ProcessError("oe_controller_staged_layout_changed")
        finally:
            os.close(fd)
    for path, expected, mode in (
        (
            operation / "control/launch.json",
            canonical(launch.model_dump(mode="json")),
            0o600,
        ),
        (operation / "input/bootstrap.json", bootstrap_bytes, 0o444),
        (operation / "input/seed.py", seed_bytes, 0o444),
        (operation / "ipc/ticket.json", canonical({"ticket": ticket}), 0o444),
    ):
        parent = _directory(path.parent)
        try:
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
            )
        finally:
            os.close(parent)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != mode
                or info.st_size != len(expected)
            ):
                raise ProcessError("oe_controller_staged_file_changed")
            data = bytearray()
            while len(data) <= len(expected):
                chunk = os.read(fd, min(65536, len(expected) + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            signature = lambda v: (
                v.st_dev,
                v.st_ino,
                v.st_size,
                v.st_mode,
                v.st_uid,
                v.st_gid,
                v.st_nlink,
                v.st_mtime_ns,
                v.st_ctime_ns,
            )
            if bytes(data) != expected or signature(info) != signature(after):
                raise ProcessError("oe_controller_staged_bytes_changed")
            result.append(signature(info))
        finally:
            os.close(fd)
    parent = _directory(operation / "ipc")
    try:
        for socket_name in ("events.sock", "broker.sock"):
            info = os.stat(socket_name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o666
            ):
                raise ProcessError("oe_controller_staged_socket_changed")
            result.append(
                (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
            )
    finally:
        os.close(parent)
    return tuple(result)


def _kernel_gone(identity):
    try:
        text = Path(f"/proc/{identity.host_pid}/stat").read_text()
    except FileNotFoundError:
        return True
    fields = text.rsplit(")", 1)[1].split()
    return int(fields[19]) != identity.start_ticks or fields[0] in {"Z", "X", "x"}


class ControllerSupervisor:
    def __init__(self, image_id, verified_image_inventory):
        if (
            re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
            or verified_image_inventory["image_id"] != image_id
        ):
            raise ValueError("Exact verified image required")
        self.image_id = image_id
        self.inventory = dict(verified_image_inventory)
        # Reuse bounded sanitized Docker CLI only, never N1 admission/launch APIs.
        self.cli = DockerSupervisor(image_id)

    def start(
        self,
        root,
        authority,
        bootstrap_bytes,
        seed_bytes,
        ticket,
        *,
        on_identity,
        before_submission=None,
    ):
        if type(authority) is not SingleUseControllerAuthority:
            raise ProcessError("oe_controller_launch_authority")
        intent = authority.intent
        a = intent.admission
        if (
            os.getuid() == 0
            or a.image_id != self.image_id
            or type(bootstrap_bytes) is not bytes
            or len(bootstrap_bytes) != intent.bootstrap_size_bytes
            or hashlib.sha256(bootstrap_bytes).hexdigest() != intent.bootstrap_sha256
            or type(seed_bytes) is not bytes
            or len(seed_bytes) != intent.seed_size_bytes
            or hashlib.sha256(seed_bytes).hexdigest() != intent.seed_sha256
            or type(ticket) is not str
            or re.fullmatch(r"[0-9a-f]{64}", ticket) is None
            or hashlib.sha256(ticket.encode()).hexdigest() != a.ipc.ticket_sha256
        ):
            raise ValueError("Controller committed input/ticket differs")
        if before_submission is not None and not callable(before_submission):
            raise TypeError("Internal submission callback required")
        authority.consume()
        root = Path(root).absolute()
        reader = listener = broker_listener = None
        with ExitStack() as guards:
            namespace = guards.enter_context(process_fence(root))
            owner = _mkdir(namespace, str(a.owner_id))
            operation = operation_path(root, a)
            if operation.exists():
                raise ProcessError("oe_controller_existing_unit_requires_recovery")
            operation = _mkdir(owner, str(a.id))
            control = _mkdir(operation, "control")
            root_fd = _directory(root)
            try:
                info = os.fstat(root_fd)
            finally:
                os.close(root_fd)
            launch = ControllerLaunch(
                schema_version="mineagent.oe-controller-launch.v1",
                intent=intent,
                runtime_name="mineagent-oe-controller-" + str(a.id),
                root_device=info.st_dev,
                root_inode=info.st_ino,
            )
            write_record(control / "launch.json", launch.model_dump(mode="json"))
            inputs = _mkdir(operation, "input")
            inputs.chmod(0o755)
            ipc = _mkdir(operation, "ipc")
            ipc.chmod(0o755)
            _write_input(inputs / "bootstrap.json", bootstrap_bytes, 0o444)
            _write_input(inputs / "seed.py", seed_bytes, 0o444)
            _write_input(ipc / "ticket.json", canonical({"ticket": ticket}), 0o444)
            seccomp = (
                Path(__file__).parents[1] / "hostile_deadline" / "seccomp.default.json"
            )
            seccomp_policy()  # exact hash before Docker consumes the host file
            fd = _directory(ipc)
            try:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(f"/proc/self/fd/{fd}/events.sock")
                os.chmod("events.sock", 0o666, dir_fd=fd, follow_symlinks=False)
                listener.listen(4)
                broker_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                broker_listener.bind(f"/proc/self/fd/{fd}/broker.sock")
                os.chmod("broker.sock", 0o666, dir_fd=fd, follow_symlinks=False)
                broker_listener.listen(2)
            except BaseException:
                if listener is not None:
                    listener.close()
                if broker_listener is not None:
                    broker_listener.close()
                raise
            finally:
                os.close(fd)
            args = [
                "create",
                "--pull=never",
                "--name",
                launch.runtime_name,
                "--log-driver",
                "none",
                "--network",
                "none",
                "--ipc",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "KILL",
                "--cap-add",
                "SETUID",
                "--cap-add",
                "SETGID",
                "--security-opt",
                "no-new-privileges",
                "--security-opt",
                f"seccomp={seccomp}",
                "--memory",
                "2g",
                "--memory-swap",
                "2g",
                "--pids-limit",
                "64",
                "--cpus",
                "1",
                "--shm-size",
                "64k",
                "--ulimit",
                "nofile=256:256",
                "--ulimit",
                "fsize=8388608:8388608",
                "--user",
                "0:0",
                "--tmpfs",
                "/tmp:" + TMPFS["/tmp"],
                "--tmpfs",
                "/work:" + TMPFS["/work"],
            ]
            for key, value in _labels(launch).items():
                args += ["--label", key + "=" + value]
            args += [
                "--mount",
                f"type=bind,src={inputs},dst=/bootstrap,readonly",
                "--mount",
                f"type=bind,src={ipc},dst=/ipc,readonly",
                a.image_id,
                str(int(intent.not_after.timestamp() * 1000)),
            ]
            try:
                staged = _staged_binding(
                    root, launch, bootstrap_bytes, seed_bytes, ticket
                )

                def fresh_submission():
                    # DB fence acquisition must never occur under namespace flock.
                    guards.close()
                    if before_submission is not None:
                        before_submission()
                    guards.enter_context(process_fence(root))
                    if (
                        _staged_binding(
                            root, launch, bootstrap_bytes, seed_bytes, ticket
                        )
                        != staged
                    ):
                        raise ProcessError("oe_controller_staged_binding_changed")

                fresh_submission()
                if time.monotonic() >= authority.deadline:
                    raise ProcessError("oe_controller_expired_before_create")
                _, raw = self.cli._command(args)
                cid = raw.decode("ascii").strip()
                if re.fullmatch(r"[a-f0-9]{64}", cid) is None:
                    raise ValueError("Exact created container ID required")
                document = self.cli._inspect(cid)
                verify_container(
                    document, launch, operation, self.inventory, container_id=cid
                )
                reader = AttachStream.connect(cid, deadline=authority.deadline)
                # Inspect before the fresh DB check, not after it: a slow daemon
                # inspection must not widen the known-stale authorization gap.
                document = self.cli._inspect(cid)
                verify_container(
                    document, launch, operation, self.inventory, container_id=cid
                )
                if document["State"]["Running"] is not False:
                    raise ProcessError("oe_controller_changed_before_start")
                fresh_submission()
                if time.monotonic() >= authority.deadline:
                    raise ProcessError("oe_controller_expired_before_start")
                self.cli._command(["start", cid])
                document = self.cli._inspect(cid)
                verify_container(
                    document, launch, operation, self.inventory, container_id=cid
                )
                if document["State"]["Running"] is not True:
                    raise ProcessError("oe_controller_not_running")
                leader = DaemonIdentityContext(
                    cid, a.image_id, document["State"]["Pid"], authority.deadline
                ).observe(document["State"]["Pid"])
                identity = ControllerIdentity(
                    schema_version="mineagent.oe-controller-identity.v1",
                    admission_sha256=digest(a.model_dump(mode="json")),
                    launch_sha256=digest(launch.model_dump(mode="json")),
                    container_id=cid,
                    image_id=a.image_id,
                    root_device=info.st_dev,
                    root_inode=info.st_ino,
                    boot_id=UUID(
                        Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                    ),
                    leader=leader,
                )
                write_record(
                    control / "identity.json", identity.model_dump(mode="json")
                )
            except BaseException:
                if reader is not None:
                    reader.close()
                if listener is not None:
                    listener.close()
                if broker_listener is not None:
                    broker_listener.close()
                # Named durable launch remains discoverable. Never repeat create
                # or treat missing/uncertain CLI response as non-submission.
                raise
        running = RunningController(
            self,
            root,
            launch,
            identity,
            authority.deadline,
            reader,
            listener,
            broker_listener,
        )
        try:
            on_identity(identity)
        except BaseException as original:
            try:
                running.finish(no_reader=True)
            except BaseException as cleanup:
                raise original from cleanup
            raise
        return running


class RunningController:
    def __init__(
        self,
        supervisor,
        root,
        launch,
        identity,
        deadline,
        reader,
        listener,
        broker_listener,
    ):
        self.supervisor, self.root, self.launch, self.identity = (
            supervisor,
            root,
            launch,
            identity,
        )
        self.deadline, self.reader, self.listener = deadline, reader, listener
        self.broker_listener = broker_listener
        self.observer_context = DaemonIdentityContext(
            identity.container_id, identity.image_id, identity.leader.host_pid, deadline
        )
        self.reports = ReportStream(reader)
        self.child = None
        self.connection = None
        self.finished = False
        self._stop_lock = threading.Lock()
        self._stop_started = False
        self._stop_record = None
        self.measured_exit_code = None
        self.measured_oom_killed = None
        self.original_deadline_completion = False
        self._completion_error = None
        self._callback_acknowledged = False
        self.operation = operation_path(root, launch.intent.admission)

    def accept_controller(self, *, on_child):
        if self.child is not None or self.finished or self._stop_started:
            raise ProcessError("oe_controller_connection_reused")
        try:
            started = self.reports.next(self.deadline)
            if started is None:
                raise ValueError("Missing trusted started report")
            self.listener.settimeout(max(0.001, self.deadline - time.monotonic()))
            connection, _ = self.listener.accept()
            self.connection = connection
            pid, uid, gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if time.monotonic() >= self.deadline:
                raise ProcessError("oe_controller_expired_peer")
            leader, child = self.observer_context.observe_pair(pid)
            if leader != self.identity.leader:
                raise ProcessError("oe_controller_leader_changed")
            before = child
            fields = dict(
                line.split(":", 1)
                for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            )
            nspids = tuple(map(int, fields["NSpid"].split()))
            if self.observer_context.observe_pair(pid) != (
                self.identity.leader,
                before,
            ):
                raise ValueError("Controller identity changed during observation")
            ControllerBinding(
                self.identity.container_id,
                self.identity.admission_sha256,
                self.identity.leader,
                child,
            )
            child_record = ControllerChild(
                schema_version="mineagent.oe-controller-child.v1",
                identity_sha256=digest(self.identity.model_dump(mode="json")),
                admission_sha256=self.identity.admission_sha256,
                controller=child,
                namespace_pids=nspids,
                namespace_pid=started["child_namespace_pid"],
                started_report_sha256=hashlib.sha256(
                    report_bytes(
                        "started",
                        started["child_namespace_pid"],
                        started["child_start_ticks"],
                    )
                ).hexdigest(),
                peer_pid=pid,
                peer_uid=uid,
                peer_gid=gid,
            )
            write_record(
                self.operation / "control" / "child.json",
                child_record.model_dump(mode="json"),
            )
            self.child = child_record
            on_child(child_record)
            return connection, ControllerBinding(
                self.identity.container_id,
                self.identity.admission_sha256,
                self.identity.leader,
                child,
            )
        except BaseException as original:
            try:
                self.finish(no_reader=True)
            except BaseException as cleanup:
                raise original from cleanup
            raise

    def finish(self, *, on_stopped=None, no_reader=False):
        if not self._stop_lock.acquire(blocking=False):
            raise ProcessError("oe_controller_stop_busy")
        try:
            if self.finished:
                if on_stopped is not None and not self._callback_acknowledged:
                    on_stopped(self._stop_record)
                    self._callback_acknowledged = True
                return self._stop_record
            if self._stop_record is not None:
                return self._complete_stop(on_stopped)
            if self._stop_started:
                raise ProcessError("oe_controller_stop_requires_recovery")
            self._stop_started = True
            return self._finish_once(on_stopped=on_stopped, no_reader=no_reader)
        finally:
            self._stop_lock.release()

    def _finish_once(self, *, on_stopped, no_reader):
        error = None
        if self.connection is not None:
            self.connection.close()
        self.listener.close()
        self.broker_listener.close()
        if not no_reader:
            try:
                events = self.reports.finish(self.deadline + 1)
                if len(events) != 2 or events[-1]["child_exit_code"] != 0:
                    raise ProcessError("oe_controller_incomplete_or_failed_reports")
            except BaseException as exc:  # noqa: BLE001 - preserve containment before propagating
                error = exc
        # Independent original PID1 deadline is authoritative even if attach is
        # unread/blocked. Kernel exit precedes closing an unread Docker reader.
        while not _kernel_gone(self.identity.leader):
            if time.monotonic() > self.deadline + 1:
                raise ProcessError("oe_controller_kernel_deadline_exceeded")
            time.sleep(0.005)
        kernel_gone_at = time.monotonic()
        if self.child is not None and not _kernel_gone(self.child.controller):
            raise ProcessError("oe_controller_child_survived")
        self.reader.close()
        document = self.supervisor.cli._inspect(self.identity.container_id)
        verify_container(
            document,
            self.launch,
            self.operation,
            self.supervisor.inventory,
            container_id=self.identity.container_id,
        )
        if (
            document["State"]["Running"] is not False
            or document["State"]["Status"] != "exited"
        ):
            raise ProcessError("oe_controller_daemon_stop_uncertain")
        state = document["State"]
        exit_code, oom_killed = state.get("ExitCode"), state.get("OOMKilled")
        self.measured_exit_code = (
            exit_code if type(exit_code) is int and 0 <= exit_code <= 255 else None
        )
        self.measured_oom_killed = oom_killed if type(oom_killed) is bool else None
        self.original_deadline_completion = (
            self.measured_exit_code == 124
            and self.measured_oom_killed is False
            and self.deadline <= kernel_gone_at <= self.deadline + 1
        )
        if not no_reader and not self.original_deadline_completion and error is None:
            error = ProcessError("oe_controller_original_deadline_completion_missing")
        # Containment remains recordable for early exit/OOM, but never successful.
        # Historical stop.v1 deliberately proves absence only, not exit cause.
        self.supervisor.cli._command(["rm", self.identity.container_id])
        if (
            self.supervisor.cli._inspect(self.identity.container_id) is not None
            or self.supervisor.cli._inspect(self.launch.runtime_name) is not None
        ):
            raise ProcessError("oe_controller_absence_uncertain")
        stop = ControllerStop(
            schema_version="mineagent.oe-controller-stop.v1",
            launch_sha256=self.identity.launch_sha256,
            identity_sha256=digest(self.identity.model_dump(mode="json")),
            child_sha256=None
            if self.child is None
            else digest(self.child.model_dump(mode="json")),
            container_id=self.identity.container_id,
            confirmed_absent=True,
            kernel_identity_absent=True,
            child_kernel_identity_absent=None if self.child is None else True,
        )
        self._stop_record = stop
        self._completion_error = error
        return self._complete_stop(on_stopped)

    def _complete_stop(self, on_stopped):
        # Replay only already-observed positive containment, never Docker actions.
        stop = self._stop_record
        with process_fence(self.root):
            root_fd = _directory(self.root)
            try:
                info = os.fstat(root_fd)
                if (info.st_dev, info.st_ino) != (
                    self.launch.root_device,
                    self.launch.root_inode,
                ):
                    raise ProcessError("oe_controller_cleanup_root_changed")
            finally:
                os.close(root_fd)
            path = self.operation / "control" / "stop.json"
            payload = stop.model_dump(mode="json")
            try:
                write_record(path, payload)
            except FileExistsError:
                if read_numeric_record(path) != payload:
                    raise ProcessError("oe_controller_stop_journal_drift") from None
            self._cleanup()
        self.finished = True
        if on_stopped is not None:
            on_stopped(stop)
            self._callback_acknowledged = True
        if self._completion_error is not None:
            raise self._completion_error
        return stop

    def _cleanup(self):
        for directory, names in [
            (
                "ipc",
                {
                    "events.sock": "socket",
                    "broker.sock": "socket",
                    "ticket.json": "file",
                },
            ),
            ("input", {"bootstrap.json": "file", "seed.py": "file"}),
        ]:
            path = self.operation / directory
            try:
                fd = _directory(path)
            except FileNotFoundError:
                continue  # Only after positive stop: prior cleanup removed it.
            try:
                remaining = set(os.listdir(fd))
                if not remaining <= set(names):
                    raise ProcessError("oe_controller_cleanup_inventory")
                # Validate every remaining inode before removing any of them.
                for name in remaining:
                    kind = names[name]
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (
                        info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or not (
                            stat.S_ISSOCK(info.st_mode)
                            if kind == "socket"
                            else stat.S_ISREG(info.st_mode)
                        )
                    ):
                        raise ProcessError("oe_controller_cleanup_identity")
                for name in remaining:
                    os.unlink(name, dir_fd=fd)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rmdir(path)
        fd = _directory(self.operation)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
