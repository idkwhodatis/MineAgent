"""Durable, fail-closed whole-container containment for the closed N1 fixture.

Trusted callers commit admission/reservation before launch and retain the shared
maintenance lifecycle through stop/cleanup. A CLI timeout is not proof that a
Docker operation did not execute. Journals never grant new database authority.
"""

import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path
from types import MappingProxyType

from packages.research_runtime.process_contracts import (
    ProcessAdmission,
    ProcessError,
    canonical,
    digest,
    validate_launch_intent,
)
from packages.research_runtime.process_profiles import profile_for

MANAGED = "mineagent-research-supervisor-v1"
MAX_RECORD = 16384
MAX_UNITS = 10000
IDENTITY_SCHEMA = "mineagent.research-process-identity.v1"
STOP_SCHEMA = "mineagent.research-process-stop.v1"


_LAUNCH_ISSUER = object()


class LaunchAuthority:
    """Opaque one-use capability issued only by committed fenced orchestration."""

    __slots__ = (
        "_admission",
        "_consume_lock",
        "_deadline",
        "_intent",
        "_issuer",
        "_used",
    )

    def __init__(self, admission, intent, *, _issuer=None):
        if _issuer is not _LAUNCH_ISSUER:
            raise ProcessError("research_process_launch_authority")
        from datetime import datetime

        validate_launch_intent(intent, admission)
        remaining = (
            datetime.fromisoformat(intent["not_after"]) - datetime.now(UTC)
        ).total_seconds()
        profile = profile_for(admission.profile, admission.role)
        if (
            intent["admission_sha256"] != digest(admission.public())
            or not 0 < remaining <= profile.wall_seconds
        ):
            raise ProcessError("research_process_launch_authority")
        self._admission, self._intent = admission, MappingProxyType(dict(intent))
        self._deadline = time.monotonic() + remaining
        self._consume_lock = threading.Lock()
        self._used = False
        self._issuer = _issuer

    @property
    def admission(self):
        return self._admission

    @property
    def intent(self):
        return self._intent

    @property
    def deadline(self):
        return self._deadline

    def consume(self):
        with self._consume_lock:
            if (
                self._issuer is not _LAUNCH_ISSUER
                or self._used
                or time.monotonic() >= self.deadline
            ):
                raise ProcessError("research_process_launch_authority")
            validate_launch_intent(dict(self.intent), self.admission)
            self._used = True
            return self.admission


def _mint_launch_authority(admission, intent):
    # Private cross-module integration seam; never an API or fixture admission.
    return LaunchAuthority(admission, intent, _issuer=_LAUNCH_ISSUER)


def _directory(path):
    path = Path(path).absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        result, fd = fd, -1
        return result
    finally:
        if fd >= 0:
            os.close(fd)


def _mkdir(parent, name):
    fd = _directory(parent)
    try:
        try:
            os.mkdir(name, mode=0o700, dir_fd=fd)
            os.fsync(fd)
        except FileExistsError:
            pass
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(child)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ProcessError("research_process_journal")
        finally:
            os.close(child)
    finally:
        os.close(fd)
    return Path(parent) / name


def write_record(path, value):
    raw = canonical(value)
    if len(raw) > MAX_RECORD:
        raise ProcessError("research_process_journal")
    parent = _directory(Path(path).parent)
    try:
        fd = os.open(
            Path(path).name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def read_record(path):
    parent = _directory(Path(path).parent)
    try:
        fd = os.open(
            Path(path).name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_RECORD
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ProcessError("research_process_journal")
        raw = os.read(fd, MAX_RECORD + 1)

        def unique(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value

        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or canonical(value) != raw:
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ProcessError("research_process_journal") from None
    finally:
        os.close(fd)


@contextmanager
def process_fence(root):
    root = Path(root).absolute()
    root_fd = _directory(root)
    namespace_fd = None
    try:
        fcntl.flock(root_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        namespace = _mkdir(root, "research-processes")
        namespace_fd = _directory(namespace)
        fcntl.flock(namespace_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield namespace
    except BlockingIOError:
        raise ProcessError("research_process_busy") from None
    finally:
        if namespace_fd is not None:
            os.close(namespace_fd)
        os.close(root_fd)


def operation_path(root, admission):
    return (
        Path(root).absolute()
        / "research-processes"
        / str(admission.owner_id)
        / str(admission.id)
    )


def _deadline_argument(intent):
    from datetime import datetime

    return str(int(datetime.fromisoformat(intent["not_after"]).timestamp() * 1000))


def _launch_value(root, admission, intent):
    info = os.stat(root, follow_symlinks=False)
    return {
        "schema_version": "mineagent.research-process-launch.v1",
        "admission": admission.public(),
        "intent": dict(intent),
        "runtime_name": "mineagent-research-" + str(admission.id),
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
    }


def load_launch(operation, expected=None, root=None):
    from uuid import UUID

    launch = read_record(Path(operation) / "control" / "launch.json")
    if (
        set(launch)
        != {
            "schema_version",
            "admission",
            "intent",
            "runtime_name",
            "root_device",
            "root_inode",
        }
        or launch["schema_version"] != "mineagent.research-process-launch.v1"
    ):
        raise ProcessError("research_process_journal")
    fields = dict(launch["admission"])
    for key in (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "logical_operation_id",
        "reservation_id",
    ):
        fields[key] = UUID(fields[key])
    admission = ProcessAdmission(**fields)
    if (
        admission.public() != launch["admission"]
        or Path(operation).name != str(admission.id)
        or Path(operation).parent.name != str(admission.owner_id)
        or launch["runtime_name"] != "mineagent-research-" + str(admission.id)
    ):
        raise ProcessError("research_process_journal")
    if expected is not None and admission != expected:
        raise ProcessError("research_process_journal")
    if root is not None and launch != _launch_value(root, admission, launch["intent"]):
        raise ProcessError("research_process_journal")
    validate_launch_intent(launch["intent"], admission)
    return launch


def validate_identity(value, launch):
    if (
        set(value)
        != {
            "schema_version",
            "launch_sha256",
            "admission_sha256",
            "container_id",
            "host_pid",
            "start_ticks",
            "pid_namespace",
            "cgroup",
            "observed_state",
        }
        or value["schema_version"] != IDENTITY_SCHEMA
        or value["launch_sha256"] != digest(launch)
        or value["admission_sha256"] != digest(launch["admission"])
    ):
        raise ProcessError("research_process_identity")
    if re.fullmatch(r"[a-f0-9]{64}", value["container_id"]) is None:
        raise ProcessError("research_process_identity")
    if value["observed_state"] in {"created", "exited"}:
        if any(
            value[k] != v
            for k, v in {
                "host_pid": 0,
                "start_ticks": 0,
                "pid_namespace": "",
                "cgroup": "",
            }.items()
        ):
            raise ProcessError("research_process_identity")
        return value
    if value["observed_state"] != "running" or any(
        type(value[k]) is not int or value[k] <= 0 for k in ("host_pid", "start_ticks")
    ):
        raise ProcessError("research_process_identity")
    if (
        type(value["pid_namespace"]) is not str
        or re.fullmatch(r"pid:\[[0-9]+\]", value["pid_namespace"]) is None
        or type(value["cgroup"]) is not str
        or not 0 < len(value["cgroup"]) <= 4096
    ):
        raise ProcessError("research_process_identity")
    return value


def validate_stopped_journal(operation, expected_admission=None):
    """Offline evidence check: never inspect/start a container during restore."""
    operation = Path(operation)
    launch = load_launch(operation, expected_admission)
    identity = validate_identity(
        read_record(operation / "control" / "identity.json"), launch
    )
    stop = read_record(operation / "control" / "stop.json")
    if stop != {
        "schema_version": STOP_SCHEMA,
        "launch_sha256": digest(launch),
        "container_id": identity["container_id"],
        "confirmed_absent": True,
    }:
        raise ProcessError("research_process_uncertain")
    if set(os.listdir(operation)) != {"control"} or set(
        os.listdir(operation / "control")
    ) != {"launch.json", "identity.json", "stop.json"}:
        raise ProcessError("research_process_journal")
    return stop


def assert_quiescent(root):
    namespace = Path(root) / "research-processes"
    if not namespace.exists() and not namespace.is_symlink():
        return
    fd = _directory(namespace)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        count = 0
        from uuid import UUID

        for owner in os.listdir(fd):
            if str(UUID(owner)) != owner:
                raise ProcessError("research_process_journal")
            owner_fd = _directory(namespace / owner)
            try:
                for unit in os.listdir(owner_fd):
                    count += 1
                    if count > MAX_UNITS or str(UUID(unit)) != unit:
                        raise ProcessError("research_process_journal")
                    operation = namespace / owner / unit
                    record = read_record(operation / "control" / "launch.json")
                    if (
                        record.get("schema_version")
                        == "mineagent.numeric-process-launch.v2"
                    ):
                        from runtimes.research_supervisor.numeric_journal import (
                            validate_numeric_quiescence,
                        )

                        validate_numeric_quiescence(operation, record)
                    else:
                        from runtimes.research_supervisor.cold_journal import (
                            validate_modern_quiescence,
                        )

                        if not validate_modern_quiescence(
                            operation, record.get("schema_version")
                        ):
                            validate_stopped_journal(operation)
            finally:
                os.close(owner_fd)
    except (OSError, ValueError, TypeError, KeyError):
        raise ProcessError("research_process_uncertain") from None
    finally:
        os.close(fd)


def process_identity(pid):
    """Observed kernel identity, never accepted from the process itself."""
    if type(pid) is not int or pid <= 0:
        raise ProcessError("research_process_identity")
    path = Path("/proc") / str(pid)
    before = (path / "stat").read_text()
    start = int(before.rsplit(")", 1)[1].split()[19])
    namespace = os.readlink(path / "ns/pid")
    group = (path / "cgroup").read_text()
    after = int((path / "stat").read_text().rsplit(")", 1)[1].split()[19])
    if start != after or not group or len(group) > 4096:
        raise ProcessError("research_process_identity")
    return {
        "host_pid": pid,
        "start_ticks": start,
        "pid_namespace": namespace,
        "cgroup": group,
    }


class DockerSupervisor:
    def __init__(self, image_id):
        if (
            type(image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        ):
            raise ProcessError("research_process_image")
        self.image_id = image_id
        self._stop_lock = threading.Lock()

    def _command(self, args, *, check=True):
        # No inherited Docker context, credentials, proxy, host secrets or env.
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/nonexistent",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
        }
        deadline = time.monotonic() + 10
        data = bytearray()
        try:
            process = subprocess.Popen(
                ["docker", "--config", "/nonexistent", *args],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            raise ProcessError("research_process_uncertain") from None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProcessError("research_process_uncertain")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(
                            key.fileobj.fileno(), min(4096, 65537 - len(data))
                        )
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ProcessError("research_process_output")
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if check and code:
                raise ProcessError("research_process_uncertain")
            return code, bytes(data)
        except subprocess.TimeoutExpired:
            raise ProcessError("research_process_uncertain") from None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()

    def _inspect(self, name):
        code, raw = self._command(["container", "inspect", name], check=False)
        if code == 1 and (b"No such container:" in raw or b"No such object:" in raw):
            return None
        if code:
            raise ProcessError("research_process_uncertain")
        try:
            result = json.loads(raw)
            if len(result) != 1:
                raise ValueError()
            return result[0]
        except (ValueError, TypeError):
            raise ProcessError("research_process_identity") from None

    def _verify(self, state, launch, operation):
        a = launch["admission"]
        labels = state["Config"]["Labels"]
        if (
            state["Image"] != self.image_id
            or state["Name"] != "/" + launch["runtime_name"]
            or any(
                labels.get(k) != v
                for k, v in {
                    "io.mineagent.managed": MANAGED,
                    "io.mineagent.admission": a["id"],
                    "io.mineagent.launch": digest(launch),
                }.items()
            )
        ):
            raise ProcessError("research_process_identity")
        host = state["HostConfig"]
        profile = profile_for(a["profile"], a["role"])
        if (
            host["NetworkMode"] != "none"
            or not host["ReadonlyRootfs"]
            or host["Privileged"]
            or host["PidMode"] != ""
            or host["IpcMode"] != "private"
            or host.get("Devices")
            or host.get("DeviceRequests")
            or host.get("DeviceCgroupRules")
            or host["Memory"] != profile.memory_bytes
            or host["MemorySwap"] != profile.memory_bytes
            or host["ShmSize"] != profile.shm_bytes
            or host["PidsLimit"] != profile.pids
            or host["NanoCpus"] != 1000000000
            or set(host["CapDrop"]) != {"ALL"}
            or host["CapAdd"]
            or host["SecurityOpt"]
            not in (["no-new-privileges"], ["no-new-privileges=true"])
        ):
            raise ProcessError("research_process_identity")
        if (
            state["Config"]["User"] != f"{os.getuid()}:{os.getgid()}"
            or host["LogConfig"]["Type"] != "none"
        ):
            raise ProcessError("research_process_identity")
        if host["Tmpfs"] != {
            "/tmp": f"rw,noexec,nosuid,nodev,size={profile.tmpfs_bytes},nr_inodes=128,mode=1777"
        }:
            raise ProcessError("research_process_identity")
        environment = state["Config"]["Env"]
        values = dict(item.split("=", 1) for item in environment)
        if (
            len(values) != len(environment)
            or set(values)
            - {
                "PATH",
                "LANG",
                "GPG_KEY",
                "PYTHON_VERSION",
                "PYTHON_SHA256",
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONUNBUFFERED",
            }
            or values.get("PYTHON_VERSION") != "3.12.14"
            or values.get("PYTHONDONTWRITEBYTECODE") != "1"
            or values.get("PYTHONUNBUFFERED") != "1"
        ):
            raise ProcessError("research_process_identity")
        binds = [mount for mount in state["Mounts"] if mount["Type"] == "bind"]
        if (
            len(binds) != 1
            or binds[0]["Destination"] != "/ipc"
            or binds[0]["RW"]
            or Path(binds[0]["Source"]) != Path(operation) / "ipc"
        ):
            raise ProcessError("research_process_identity")
        if any(
            mount["Type"] != "bind"
            and not (
                mount["Type"] == "tmpfs"
                and mount["Destination"] in {"/tmp", "/dev/shm"}
            )
            for mount in state["Mounts"]
        ):
            raise ProcessError("research_process_identity")
        if state["Config"]["Entrypoint"] != ["python"] or state["Config"]["Cmd"] != [
            "-I",
            "/opt/mineagent/deadline.py",
            _deadline_argument(launch["intent"]),
        ]:
            raise ProcessError("research_process_identity")
        return state

    def launch(self, root, authority, ticket):
        if type(authority) is not LaunchAuthority:
            raise ProcessError("research_process_launch_authority")
        admission = authority.consume()
        if (
            os.getuid() == 0
            or admission.image_id != self.image_id
            or type(ticket) is not str
            or len(ticket) != 64
            or hashlib.sha256(ticket.encode()).hexdigest() != admission.ticket_sha256
        ):
            raise ProcessError("research_process_identity")
        profile = profile_for(admission.profile, admission.role)
        with process_fence(root) as namespace:
            owner = _mkdir(namespace, str(admission.owner_id))
            operation = _mkdir(owner, str(admission.id))
            control = _mkdir(operation, "control")
            ipc = _mkdir(operation, "ipc")
            launch = _launch_value(root, admission, authority.intent)
            # Duplicate launch is always an error, including an uncertain first call.
            write_record(control / "launch.json", launch)
            write_record(ipc / "ticket.json", {"ticket": ticket})
            args = [
                "create",
                "--pull=never",
                "--name",
                launch["runtime_name"],
                "--label",
                f"io.mineagent.managed={MANAGED}",
                "--label",
                f"io.mineagent.admission={admission.id}",
                "--label",
                f"io.mineagent.launch={digest(launch)}",
                "--network=none",
                "--ipc=private",
                f"--shm-size={profile.shm_bytes}",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cpus=1",
                f"--memory={profile.memory_bytes}",
                f"--memory-swap={profile.memory_bytes}",
                f"--pids-limit={profile.pids}",
                "--ulimit",
                "nofile=64:64",
                "--ulimit",
                "core=0:0",
                "--ulimit",
                "fsize=16384:16384",
                "--log-driver=none",
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,nodev,size={profile.tmpfs_bytes},nr_inodes=128,mode=1777",
                "--mount",
                f"type=bind,src={ipc},dst=/ipc,readonly",
                "--entrypoint",
                "python",
                self.image_id,
                "-I",
                "/opt/mineagent/deadline.py",
                _deadline_argument(authority.intent),
            ]
            _, raw = self._command(args)
            container = raw.decode().strip()
            if re.fullmatch(r"[a-f0-9]{64}", container) is None:
                raise ProcessError("research_process_identity")
            state = self._verify(self._inspect(container), launch, operation)
            self._command(["start", container])
            state = self._verify(self._inspect(container), launch, operation)
            identity = {
                "schema_version": IDENTITY_SCHEMA,
                "launch_sha256": digest(launch),
                "admission_sha256": digest(launch["admission"]),
                "container_id": container,
                "observed_state": "running",
                **process_identity(state["State"]["Pid"]),
            }
            validate_identity(identity, launch)
            write_record(control / "identity.json", identity)
            return identity

    def stop_and_verify(self, root, admission):
        """Serialize hostdeadline and callercleanup without releasing protection."""
        if not self._stop_lock.acquire(timeout=45):
            raise ProcessError("research_process_uncertain")
        try:
            return self._stop_and_verify(root, admission)
        finally:
            self._stop_lock.release()

    def _stop_and_verify(self, root, admission):
        """Positive exact-unit containment only; works after attempt lease loss."""
        with process_fence(root):
            operation = operation_path(root, admission)
            launch = load_launch(operation, admission, root)
            if (operation / "control" / "stop.json").exists():
                return validate_stopped_journal(operation, admission)
            state = self._inspect(launch["runtime_name"])
            identity_path = operation / "control" / "identity.json"
            if identity_path.exists():
                identity = validate_identity(read_record(identity_path), launch)
            elif state is not None:
                self._verify(state, launch, operation)
                status = state["State"]["Status"]
                if status not in {"created", "exited", "running"}:
                    raise ProcessError("research_process_uncertain")
                kernel = (
                    process_identity(state["State"]["Pid"])
                    if status == "running"
                    else {
                        "host_pid": 0,
                        "start_ticks": 0,
                        "pid_namespace": "",
                        "cgroup": "",
                    }
                )
                identity = {
                    "schema_version": IDENTITY_SCHEMA,
                    "launch_sha256": digest(launch),
                    "admission_sha256": digest(launch["admission"]),
                    "container_id": state["Id"],
                    "observed_state": status,
                    **kernel,
                }
                validate_identity(identity, launch)
                write_record(identity_path, identity)
            else:
                # Missing create response/identity plus absence is not non-submission.
                raise ProcessError("research_process_uncertain")
            if state is not None:
                self._verify(state, launch, operation)
                if state["Id"] != identity["container_id"]:
                    raise ProcessError("research_process_identity")
                self._command(["rm", "--force", identity["container_id"]])
            if (
                self._inspect(identity["container_id"]) is not None
                or self._inspect(launch["runtime_name"]) is not None
            ):
                raise ProcessError("research_process_uncertain")
            ipc = operation / "ipc"
            if ipc.exists():
                fd = _directory(ipc)
                try:
                    for name in os.listdir(fd):
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if (
                            name == "ticket.json"
                            and stat.S_ISREG(info.st_mode)
                            and info.st_nlink == 1
                            or name == "peer.sock"
                            and stat.S_ISSOCK(info.st_mode)
                        ):
                            os.unlink(name, dir_fd=fd)
                        else:
                            raise ProcessError("research_process_journal")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.rmdir(ipc)
            stop = {
                "schema_version": STOP_SCHEMA,
                "launch_sha256": digest(launch),
                "container_id": identity["container_id"],
                "confirmed_absent": True,
            }
            write_record(operation / "control" / "stop.json", stop)
            return validate_stopped_journal(operation, admission)


class UnitWatchdog:
    """Trusted host wall limit; the fixture cannot extend or disable its timer."""

    def __init__(self, supervisor, root, authority):
        self.supervisor, self.root, self.authority = supervisor, root, authority
        self.cancelled = threading.Event()
        self.expired = threading.Event()
        self.error = None
        self.thread = threading.Thread(
            target=self._watch, name="neutral-unit-deadline", daemon=False
        )

    def _watch(self):
        if self.cancelled.wait(max(0, self.authority.deadline - time.monotonic())):
            return
        self.expired.set()
        try:
            self.supervisor.stop_and_verify(self.root, self.authority.admission)
        except BaseException as exc:  # noqa: BLE001 -- retain every deadline containment failure
            self.error = exc

    def start(self):
        self.thread.start()

    def stopped(self):
        # Called only after positive containment, never just because caller exits.
        self.cancelled.set()
        self.thread.join(timeout=45)
        if self.thread.is_alive():
            raise ProcessError("research_process_uncertain")
