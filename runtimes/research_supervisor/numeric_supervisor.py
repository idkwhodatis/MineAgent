"""Operational numeric whole-unit supervisor; production issuance remains closed.

Consumes a private, one-use post-commit authority. It never imports generated
source or native parsers on the host. Docker mutations follow a durable launch
journal; protected extraction does not imply stop or result publication.
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from packages.research_runtime.numeric_process_journal import (
    NumericExtraction,
    NumericIdentity,
    NumericIntent,
    NumericLaunch,
    NumericStop,
    validate_numeric_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.factor_execution.acceptance import (
    read_numeric_frame,
    require_complete_numeric_stream,
    require_live_numeric_frame,
)
from runtimes.factor_execution.attach_stream import AttachStream
from runtimes.factor_execution.protected_output import MAGIC, validate_export
from runtimes.factor_execution.runtime_contract import (
    BASE_IMAGE,
    MASKED_PATHS,
    image_inventory,
    verify_inspect,
)
from runtimes.research_supervisor.numeric_journal import (
    read_numeric_record as read_record,
)
from runtimes.research_supervisor.supervisor import (
    _directory,
    _mkdir,
    operation_path,
    process_fence,
    write_record,
)

_ISSUER = object()


class NumericAuthority:
    """Opaque one-use obligation, minted only after durable fenced lookup."""

    __slots__ = (
        "_issuer",
        "_lock",
        "_used",
        "deadline",
        "inputs",
        "intent",
        "inventory_bytes",
    )

    def __init__(self, issuer, intent, inputs, inventory):
        if issuer is not _ISSUER:
            raise ProcessError("numeric_process_authority")
        intent = NumericIntent.model_validate(intent)
        check_input_bytes(intent, inputs)
        remaining = (intent.not_after - datetime.now(UTC)).total_seconds()
        if datetime.now(UTC) < intent.issued_at or not 0 < remaining <= 10:
            raise ProcessError("numeric_process_deadline")
        self._issuer = issuer
        self.intent = intent
        self.inputs = MappingProxyType(dict(inputs))
        self.inventory_bytes = canonical(inventory)
        self.deadline = time.monotonic() + remaining
        self._lock, self._used = threading.Lock(), False

    def consume(self):
        with self._lock:
            if (
                self._issuer is not _ISSUER
                or self._used
                or time.monotonic() >= self.deadline
                or datetime.now(UTC) >= self.intent.not_after
            ):
                raise ProcessError("numeric_process_authority")
            self._used = True
        check_input_bytes(self.intent, self.inputs)
        return json.loads(self.inventory_bytes)


def _mint_numeric_authority(intent, inputs, inventory):
    return NumericAuthority(_ISSUER, intent, inputs, inventory)


def check_input_bytes(intent, inputs):
    intent = NumericIntent.model_validate(intent)
    expected = {entry.destination: entry for entry in intent.inputs.files}
    if set(inputs) != set(expected) or any(
        type(value) is not bytes for value in inputs.values()
    ):
        raise ProcessError("numeric_process_input")
    for name, value in inputs.items():
        entry = expected[name]
        if (
            len(value) != entry.size_bytes
            or hashlib.sha256(value).hexdigest() != entry.sha256
        ):
            raise ProcessError("numeric_process_input")
    # Prefix JSON is bounded host metadata, never a native output parser.
    admission = intent.admission
    if admission.role == "factor_cutoff_candidate":
        from packages.rdagent_adapter.prefix import PrefixManifest

        prefix = PrefixManifest.model_validate_json(inputs["/input/manifest.json"])
        if (
            prefix.host_cutoff != intent.inputs.cutoff
            or prefix.candidate_image != admission.image_id
            or prefix.candidate_profile != admission.profile
        ):
            raise ProcessError("numeric_process_prefix")
    else:
        # Full prefix-to-normalizer binding is resolved by the persisted native
        # consumer before issuance; here additionally close every wire field.
        manifest = json.loads(inputs["/input/manifest.json"])
        if (
            type(manifest) is not dict
            or set(manifest)
            != {
                "schema_version",
                "hdf_sha256",
                "listings_sha256",
                "output_event_ns",
                "max_rows",
                "max_bytes",
            }
            or canonical(manifest) != inputs["/input/manifest.json"]
            or manifest["schema_version"] != "mineagent.factor-normalization.v1"
            or manifest["hdf_sha256"] != expected["/input/result.h5"].sha256
            or manifest["listings_sha256"] != expected["/input/listings.json"].sha256
            or any(
                type(manifest[k]) is not int
                for k in ("output_event_ns", "max_rows", "max_bytes")
            )
            or not 1 <= manifest["max_rows"] <= 10000
            or not 1024 <= manifest["max_bytes"] <= 16_777_216
        ):
            raise ProcessError("numeric_process_prefix")


def _write_bytes(path, content, mode):
    directory = _directory(path.parent)
    fd = None
    try:
        fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
            dir_fd=directory,
        )
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ProcessError("numeric_process_output")
            view = view[written:]
        os.fsync(fd)
        os.fsync(directory)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _boot_id():
    return UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def _observe(pid):
    if type(pid) is not int or pid <= 1:
        raise ProcessError("numeric_process_kernel_identity")
    path = Path("/proc") / str(pid)
    before = path.joinpath("stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(before[19])
    namespace = os.readlink(path / "ns/pid")
    match = re.fullmatch(r"pid:\[([0-9]+)\]", namespace)
    after = path.joinpath("stat").read_text().rsplit(")", 1)[1].split()
    if match is None or ticks != int(after[19]) or before[0] in {"Z", "X", "x"}:
        raise ProcessError("numeric_process_kernel_identity")
    status = dict(
        line.split(":", 1)
        for line in path.joinpath("status").read_text().splitlines()
        if ":" in line
    )
    pids = [int(value) for value in status["NSpid"].split()]
    if len(pids) < 2 or pids[0] != pid or pids[-1] != 1:
        raise ProcessError("numeric_process_kernel_identity")
    return ticks, int(match[1]), _boot_id()


def kernel_identity_absent(identity):
    if _boot_id() != identity.boot_id:
        return True
    try:
        fields = (
            (Path("/proc") / str(identity.init_pid) / "stat")
            .read_text()
            .rsplit(")", 1)[1]
            .split()
        )
    except FileNotFoundError:
        return True
    # PID reuse is not the original unit. Zombies retain identity, so do not
    # falsely claim disappearance while the original init still has a task slot.
    return int(fields[19]) != identity.init_start_ticks


class NumericDockerClient:
    """Bounded local CLI transport, no inherited secrets/context and no launch API."""

    def __init__(self):
        self.deadline = None

    def _command(self, args, *, check=True):
        deadline = (
            min(time.monotonic() + 10, self.deadline)
            if self.deadline is not None
            else time.monotonic() + 10
        )
        if time.monotonic() >= deadline:
            raise ProcessError("numeric_process_command_deadline")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/nonexistent",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
        }
        process = None
        data = bytearray()
        try:
            process = subprocess.Popen(
                ["docker", "--config", "/nonexistent", *args],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProcessError("numeric_process_command_deadline")
                    for key, _events in selector.select(remaining):
                        chunk = os.read(
                            key.fileobj.fileno(), min(4096, 65537 - len(data))
                        )
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ProcessError("numeric_process_command_output")
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if time.monotonic() > deadline:
                raise ProcessError("numeric_process_command_deadline")
            if check and code:
                raise ProcessError("numeric_process_command_failed")
            return code, bytes(data)
        except (OSError, subprocess.TimeoutExpired):
            raise ProcessError("numeric_process_command_failed") from None
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            # The CLI exited between poll and killpg. This is
                            # not evidence about the container it may have made.
                            pass
                        process.wait(
                            timeout=max(0.001, min(1, deadline - time.monotonic()))
                        )
                except (OSError, subprocess.TimeoutExpired):
                    # Keep the supervisor's bounded fallback containment path
                    # reachable; never leak an unsanitized cleanup exception.
                    raise ProcessError("numeric_process_command_cleanup") from None
                finally:
                    process.stdout.close()

    def _inspect(self, name):
        code, raw = self._command(["container", "inspect", name], check=False)
        if code == 1 and (b"No such container:" in raw or b"No such object:" in raw):
            return None
        if code:
            raise ProcessError("numeric_process_uncertain")
        try:
            values = json.loads(raw)
            if (
                type(values) is not list
                or len(values) != 1
                or type(values[0]) is not dict
            ):
                raise ValueError()
            return values[0]
        except (ValueError, TypeError):
            raise ProcessError("numeric_process_runtime_identity") from None


class NumericSupervisor:
    def __init__(self, image_id):
        if (
            type(image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        ):
            raise ProcessError("numeric_process_image")
        self.image_id = image_id
        self.client = NumericDockerClient()
        self._inventory = None
        self._stop_lock = threading.Lock()

    def verified_inventory(self, role):
        endpoint = Path("/var/run/docker.sock")
        if not stat.S_ISSOCK(endpoint.stat().st_mode):
            raise ProcessError("numeric_process_local_daemon")
        _, base = self.client._command(["image", "inspect", BASE_IMAGE])
        _, image = self.client._command(["image", "inspect", self.image_id])
        return image_inventory(json.loads(base)[0], json.loads(image)[0], role=role)

    def _paths(self, root, admission):
        operation = operation_path(root, admission)
        inputs = {
            entry.destination: str(operation / "inputs" / Path(entry.destination).name)
            for entry in self._launch(operation).intent.inputs.files
        }
        return operation, inputs

    def _launch(self, operation):
        return NumericLaunch.model_validate_json(
            canonical(read_record(operation / "control" / "launch.json"))
        )

    def _verify(self, state, launch, inputs, inventory):
        admission = launch.intent.admission
        role = (
            "candidate" if admission.role == "factor_cutoff_candidate" else "normalizer"
        )
        labels = state.get("Config", {}).get("Labels") or {}
        if (
            state.get("Name") != "/" + launch.runtime_name
            or state.get("Image") != admission.image_id
            or labels.get("io.mineagent.numeric.admission") != str(admission.id)
            or labels.get("io.mineagent.numeric.launch")
            != digest(launch.model_dump(mode="json"))
            or state.get("HostConfig", {}).get("RestartPolicy")
            != {"Name": "no", "MaximumRetryCount": 0}
        ):
            raise ProcessError("numeric_process_runtime_identity")
        verify_inspect(
            state,
            image=admission.image_id,
            inputs=inputs,
            role=role,
            deadline_ms=str(int(launch.intent.not_after.timestamp() * 1000)),
            inventory=inventory,
            masked_paths=sorted(
                [
                    *MASKED_PATHS,
                    *(
                        str(p)
                        for p in Path("/sys/devices/system/cpu").glob(
                            "cpu[0-9]*/thermal_throttle"
                        )
                    ),
                ]
            ),
        )
        return state

    def execute(self, root, authority, *, on_identity):
        if type(authority) is not NumericAuthority:
            raise ProcessError("numeric_process_authority")
        inventory = authority.consume()
        self._inventory = inventory
        self.client.deadline = authority.deadline + 1
        intent = authority.intent
        admission = intent.admission
        if admission.image_id != self.image_id or MAGIC != b"MANU2\x00":
            raise ProcessError("numeric_process_runtime_identity")
        root = Path(root).absolute()
        if any(value in str(root) for value in (",", "\n", "\r")):
            raise ProcessError("numeric_process_root")
        reader = None
        launch = None
        try:
            with process_fence(root) as namespace:
                owner = _mkdir(namespace, str(admission.owner_id))
                operation = owner / str(admission.id)
                # Opaque authority and O_EXCL filesystem identity both prevent replay.
                os.mkdir(operation, 0o700)
                owner_fd = _directory(owner)
                try:
                    os.fsync(owner_fd)
                finally:
                    os.close(owner_fd)
                control = _mkdir(operation, "control")
                source = _mkdir(operation, "inputs")
                _mkdir(operation, "output")
                root_fd = _directory(root)
                try:
                    info = os.fstat(root_fd)
                finally:
                    os.close(root_fd)
                launch = NumericLaunch(
                    schema_version="mineagent.numeric-process-launch.v2",
                    intent=intent,
                    runtime_name="mineagent-numeric-" + str(admission.id),
                    root_device=info.st_dev,
                    root_inode=info.st_ino,
                )
                # Durable binding exists before any Docker mutation, even create.
                write_record(control / "launch.json", launch.model_dump(mode="json"))
                inputs = {}
                for destination, content in authority.inputs.items():
                    path = source / Path(destination).name
                    _write_bytes(path, content, 0o444)
                    inputs[destination] = str(path)
                if time.monotonic() >= authority.deadline:
                    raise ProcessError("numeric_process_deadline")
                args = self._create_args(launch, inputs)
                _, raw = self.client._command(args)
                cid = raw.decode("ascii").strip()
                if re.fullmatch(r"[a-f0-9]{64}", cid) is None:
                    raise ProcessError("numeric_process_runtime_identity")
                self._verify(self.client._inspect(cid), launch, inputs, inventory)
                reader = AttachStream.connect(cid, deadline=authority.deadline)
                if (
                    time.monotonic() >= authority.deadline
                    or datetime.now(UTC) >= intent.not_after
                ):
                    raise ProcessError("numeric_process_deadline")
                self.client._command(["start", cid])
                state = self._verify(
                    self.client._inspect(cid), launch, inputs, inventory
                )
                identity = self._observe_identity(state, launch)
                write_record(
                    control / "identity.json", identity.model_dump(mode="json")
                )
            # No namespace flock or database transaction spans stream waiting.
            on_identity(identity)
            kind, payload = read_numeric_frame(reader, authority.deadline)
            require_live_numeric_frame(
                self.client._inspect(cid), time.monotonic(), authority.deadline
            )
            if kind != 2:
                raise ProcessError("numeric_process_output_rejected")
            basename = (
                "result.h5"
                if admission.role == "factor_cutoff_candidate"
                else "values.parquet"
            )
            content = validate_export(payload, expected_basename=basename)
            # Do not persist any successful extraction until the channel reaches
            # EOF with no trailing/duplicate bytes under the original unit deadline.
            require_complete_numeric_stream(reader, authority.deadline + 1)
            while True:
                stopped = self.client._inspect(cid)
                if stopped is None or time.monotonic() > authority.deadline + 1:
                    raise ProcessError("numeric_process_original_deadline")
                if stopped["State"]["Running"] is False:
                    if stopped["State"]["ExitCode"] != 124:
                        raise ProcessError("numeric_process_original_deadline")
                    break
                time.sleep(0.01)
            extraction = NumericExtraction(
                schema_version="mineagent.numeric-process-extraction.v2",
                launch_sha256=digest(launch.model_dump(mode="json")),
                identity_sha256=digest(identity.model_dump(mode="json")),
                protocol="MANU2",
                basename=basename,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                protected_frame_complete=True,
                producer_quiesced=True,
            )
            with process_fence(root):
                _write_bytes(operation / "output" / basename, content, 0o600)
                write_record(
                    control / "extraction.json", extraction.model_dump(mode="json")
                )
            return extraction
        finally:
            if reader is not None:
                reader.close()
            if launch is not None:
                try:
                    self.stop_and_verify(
                        root, admission, original_deadline=authority.deadline + 1
                    )
                except ProcessError:
                    # Deliberately separate overdue containment from timely
                    # result eligibility; no fresh budget can rescue a result.
                    self.stop_and_verify(root, admission)
                    raise ProcessError("numeric_process_original_deadline") from None

    def _create_args(self, launch, inputs):
        seccomp = (
            Path(__file__).parents[1] / "hostile_deadline" / "seccomp.default.json"
        )
        args = [
            "create",
            "--name",
            launch.runtime_name,
            "--restart",
            "no",
            "--label",
            "io.mineagent.numeric.admission=" + str(launch.intent.admission.id),
            "--label",
            "io.mineagent.numeric.launch=" + digest(launch.model_dump(mode="json")),
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
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "KILL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            f"seccomp={seccomp.resolve()}",
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--pids-limit",
            "16",
            "--cpus",
            "1",
            "--user",
            "0:0",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=4m,uid=65533,gid=65533,mode=0700",
            "--tmpfs",
            "/output:rw,noexec,nosuid,nodev,size=16m,uid=0,gid=0,mode=0733",
            "--tmpfs",
            "/status:rw,noexec,nosuid,nodev,size=64k,uid=0,gid=0,mode=0700",
        ]
        for destination, source in sorted(inputs.items()):
            args.extend(
                ["--mount", f"type=bind,src={source},dst={destination},readonly"]
            )
        args.extend(
            [self.image_id, str(int(launch.intent.not_after.timestamp() * 1000))]
        )
        return args

    def _observe_identity(self, state, launch):
        if state["State"]["Running"] is not True:
            raise ProcessError("numeric_process_identity_unobserved")
        pid = state["State"]["Pid"]
        ticks, namespace, boot = _observe(pid)
        return NumericIdentity(
            schema_version="mineagent.numeric-process-identity.v2",
            admission_sha256=digest(launch.intent.admission.model_dump(mode="json")),
            launch_sha256=digest(launch.model_dump(mode="json")),
            container_id=state["Id"],
            image_id=self.image_id,
            root_device=launch.root_device,
            root_inode=launch.root_inode,
            init_pid=pid,
            init_start_ticks=ticks,
            boot_id=boot,
            pid_namespace_inode=namespace,
        )

    def stop_and_verify(self, root, admission, *, original_deadline=None):
        budget = (
            original_deadline
            if original_deadline is not None
            else time.monotonic() + 30
        )
        if not self._stop_lock.acquire(
            timeout=max(0, min(30, budget - time.monotonic()))
        ):
            raise ProcessError("numeric_process_uncertain")
        previous_deadline = self.client.deadline
        self.client.deadline = budget
        try:
            with process_fence(root):
                operation, inputs = self._paths(root, admission)
                launch = self._launch(operation)
                if launch.intent.admission != admission:
                    raise ProcessError("numeric_process_journal_identity")
                root_fd = _directory(root)
                try:
                    info = os.fstat(root_fd)
                finally:
                    os.close(root_fd)
                if (info.st_dev, info.st_ino) != (
                    launch.root_device,
                    launch.root_inode,
                ):
                    raise ProcessError("numeric_process_root_identity")
                stop_path = operation / "control" / "stop.json"
                identity_path = operation / "control" / "identity.json"
                state = self.client._inspect(launch.runtime_name)
                role = (
                    "candidate"
                    if admission.role == "factor_cutoff_candidate"
                    else "normalizer"
                )
                if state is not None:
                    self._verify(state, launch, inputs, self.verified_inventory(role))
                if identity_path.exists():
                    identity = NumericIdentity.model_validate_json(
                        canonical(read_record(identity_path))
                    )
                elif state is not None:
                    # Exact immutable Docker name/image/profile/launch labels
                    # were verified above. Missing kernel identity never permits
                    # a positive stop, but must not strand our exact container.
                    if state["State"]["Running"] is True:
                        try:
                            identity = self._observe_identity(state, launch)
                        except (OSError, ProcessError):
                            self._cleanup_unobserved(state, launch)
                            raise ProcessError(
                                "numeric_process_identity_unobserved"
                            ) from None
                        write_record(identity_path, identity.model_dump(mode="json"))
                    else:
                        self._cleanup_unobserved(state, launch)
                        created = state["State"].get("Status") == "created" and state[
                            "State"
                        ].get("StartedAt") in (None, "0001-01-01T00:00:00Z")
                        raise ProcessError(
                            "numeric_process_created_cleanup_uncertain"
                            if created
                            else "numeric_process_identity_unobserved"
                        )
                else:
                    # Missing start/identity response is NOT proof of no work.
                    # Never invent a fake PID or v1 created-state identity.
                    raise ProcessError("numeric_process_identity_unobserved")
                if (
                    identity.admission_sha256
                    != digest(admission.model_dump(mode="json"))
                    or identity.launch_sha256 != digest(launch.model_dump(mode="json"))
                    or identity.image_id != admission.image_id
                ):
                    raise ProcessError("numeric_process_journal_identity")
                if state is not None:
                    if state["Id"] != identity.container_id:
                        raise ProcessError("numeric_process_runtime_identity")
                    if state["State"]["Running"] is True:
                        ticks, namespace, boot = _observe(state["State"]["Pid"])
                        if (state["State"]["Pid"], ticks, namespace, boot) != (
                            identity.init_pid,
                            identity.init_start_ticks,
                            identity.pid_namespace_inode,
                            identity.boot_id,
                        ):
                            raise ProcessError("numeric_process_kernel_identity")
                    self.client._command(["rm", "--force", identity.container_id])
                kernel_deadline = min(time.monotonic() + 2, budget)
                while (
                    not kernel_identity_absent(identity)
                    and time.monotonic() < kernel_deadline
                ):
                    time.sleep(0.01)
                if (
                    self.client._inspect(identity.container_id) is not None
                    or self.client._inspect(launch.runtime_name) is not None
                    or not kernel_identity_absent(identity)
                ):
                    raise ProcessError("numeric_process_uncertain")
                stop = NumericStop(
                    schema_version="mineagent.numeric-process-stop.v2",
                    launch_sha256=identity.launch_sha256,
                    identity_sha256=digest(identity.model_dump(mode="json")),
                    container_id=identity.container_id,
                    confirmed_absent=True,
                    kernel_identity_absent=True,
                    observed_at=datetime.now(UTC),
                    deadline_bound_met=original_deadline is not None
                    and time.monotonic() <= original_deadline,
                )
                validate_numeric_journal(launch, identity, stop)
                if stop_path.exists():
                    existing = NumericStop.model_validate_json(
                        canonical(read_record(stop_path))
                    )
                    validate_numeric_journal(launch, identity, existing)
                    self._remove_inputs(operation, launch, allow_missing=True)
                    return existing
                # Validate all mounted bytes before making the durable stop/input
                # verification checkpoint; cleanup can then resume after a crash.
                self._remove_inputs(operation, launch, verify_only=True)
                if original_deadline is not None and (
                    time.monotonic() > original_deadline or not stop.deadline_bound_met
                ):
                    raise ProcessError("numeric_process_original_deadline")
                write_record(stop_path, stop.model_dump(mode="json"))
                self._remove_inputs(operation, launch, allow_missing=True)
                return stop
        finally:
            self.client.deadline = previous_deadline
            self._stop_lock.release()

    def _cleanup_unobserved(self, state, launch):
        """Contain an already verified exact Docker unit, without a stop claim.

        No PID, stop journal, input cleanup, result or accounting settlement is
        manufactured. A lost start response may still have executed work.
        """
        cid = state.get("Id")
        if type(cid) is not str or re.fullmatch(r"[a-f0-9]{64}", cid) is None:
            raise ProcessError("numeric_process_runtime_identity")
        self.client._command(["rm", "--force", cid])
        if (
            self.client._inspect(cid) is not None
            or self.client._inspect(launch.runtime_name) is not None
        ):
            raise ProcessError("numeric_process_uncertain")

    def _remove_inputs(
        self, operation, launch, *, allow_missing=False, verify_only=False
    ):
        try:
            directory = _directory(operation / "inputs")
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        try:
            expected = {
                Path(item.destination).name: item for item in launch.intent.inputs.files
            }
            present = set(os.listdir(directory))
            if not present <= set(expected) or (
                not allow_missing and present != set(expected)
            ):
                raise ProcessError("numeric_process_input")
            for name in present:
                item = expected[name]
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                try:
                    info = os.fstat(fd)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_size != item.size_bytes
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o444
                    ):
                        raise ProcessError("numeric_process_input")
                    chunks, remaining = [], item.size_bytes + 1
                    while remaining:
                        chunk = os.read(fd, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    content = b"".join(chunks)
                    after = os.fstat(fd)
                    if any(
                        getattr(info, key) != getattr(after, key)
                        for key in (
                            "st_dev",
                            "st_ino",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                            "st_nlink",
                            "st_uid",
                            "st_mode",
                        )
                    ):
                        raise ProcessError("numeric_process_input")
                    if (
                        len(content) != item.size_bytes
                        or hashlib.sha256(content).hexdigest() != item.sha256
                    ):
                        raise ProcessError("numeric_process_input")
                finally:
                    os.close(fd)
            if verify_only:
                return
            for name in present:
                os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
        os.rmdir(operation / "inputs")
        parent = _directory(operation)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
