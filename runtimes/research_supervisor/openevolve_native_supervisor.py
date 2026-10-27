"""Operational OE native observation whole-unit supervisor; production issuance remains closed.

Consumes a private, one-use post-commit authority. It never imports generated
source or native parsers on the host. Docker mutations follow a durable launch
journal; protected extraction does not imply stop or result publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.factor_execution.attach_stream import AttachStream
from runtimes.openevolve_evaluator.journal import (
    NativeExtraction,
    NativeIdentity,
    NativeIntent,
    NativeLaunch,
    NativeStop,
    validate_native_journal,
)
from runtimes.openevolve_evaluator.protected_output import (
    MAGIC,
    validate_export,
)
from runtimes.openevolve_evaluator.runtime_contract import (
    MASKED_PATHS,
    image_inventory,
    verify_inspect,
)
from runtimes.openevolve_evaluator.transport import (
    read_native_frame,
    require_complete_native_stream,
    require_live_native_frame,
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


class NativeAuthority:
    """Opaque one-use obligation, minted only after durable fenced lookup."""

    __slots__ = (
        "_issuer",
        "_lock",
        "_used",
        "deadline",
        "inputs",
        "intent",
        "inventory_bytes",
        "manifest",
    )

    def __init__(self, issuer, intent, manifest, inputs, inventory):
        if issuer is not _ISSUER:
            raise ProcessError("oe_native_process_authority")
        intent = NativeIntent.model_validate(intent)
        check_input_bytes(intent, manifest, inputs)
        remaining = (intent.not_after - datetime.now(UTC)).total_seconds()
        if datetime.now(UTC) < intent.issued_at or not 0 < remaining <= 60:
            raise ProcessError("oe_native_process_deadline")
        self._issuer = issuer
        self.intent = intent
        self.manifest = intent.inputs.require_manifest(manifest)
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
                raise ProcessError("oe_native_process_authority")
            self._used = True
        check_input_bytes(self.intent, self.manifest, self.inputs)
        return json.loads(self.inventory_bytes)


def _mint_native_authority(intent, manifest, inputs, inventory):
    return NativeAuthority(_ISSUER, intent, manifest, inputs, inventory)


def check_input_bytes(intent, manifest, inputs):
    from runtimes.openevolve_evaluator.contracts import check_input_bytes as check

    intent = NativeIntent.model_validate(intent)
    manifest = intent.inputs.require_manifest(manifest)
    if set(inputs) != {item.destination for item in manifest.files}:
        raise ProcessError("oe_native_process_input")
    check(manifest, {path.removeprefix("/input/"): raw for path, raw in inputs.items()})


def staged_name(destination):
    return hashlib.sha256(destination.encode("ascii")).hexdigest()


def read_manifest(operation, launch):
    from runtimes.openevolve_evaluator.contracts import parse_manifest

    directory = _directory(operation)
    try:
        fd = os.open(
            "manifest.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 < before.st_size <= 65536
        ):
            raise ProcessError("oe_native_manifest_inode")
        raw = bytearray()
        while len(raw) <= 65536:
            block = os.read(fd, min(65536, 65537 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or len(raw) > 65536
            or any(
                getattr(before, k) != getattr(after, k)
                for k in (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                    "st_mode",
                    "st_uid",
                    "st_nlink",
                )
            )
        ):
            raise ProcessError("oe_native_manifest_changed")
        return launch.intent.inputs.require_manifest(parse_manifest(bytes(raw)))
    finally:
        os.close(fd)


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
                raise ProcessError("oe_native_process_output")
            view = view[written:]
        os.fsync(fd)
        os.fsync(directory)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _boot_id():
    return UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())


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


from runtimes.research_supervisor.numeric_supervisor import (
    NumericDockerClient as NativeDockerClient,
)


class NativeSupervisor:
    def __init__(self, image_id, installed_parent_image_id):
        if (
            type(image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        ):
            raise ProcessError("oe_native_process_image")
        if re.fullmatch(r"sha256:[a-f0-9]{64}", installed_parent_image_id) is None:
            raise ProcessError("oe_native_process_parent_image")
        self.installed_parent_image_id = installed_parent_image_id
        self.image_id = image_id
        self.client = NativeDockerClient()
        self._inventory = None
        self._stop_lock = threading.Lock()

    def verified_inventory(self, role):
        endpoint = Path("/var/run/docker.sock")
        if not stat.S_ISSOCK(endpoint.stat().st_mode):
            raise ProcessError("oe_native_process_local_daemon")
        _, base = self.client._command(
            ["image", "inspect", self.installed_parent_image_id]
        )
        _, image = self.client._command(["image", "inspect", self.image_id])
        base, image = json.loads(base)[0], json.loads(image)[0]
        if (
            base.get("Id") != self.installed_parent_image_id
            or image.get("Id") != self.image_id
        ):
            raise ProcessError("oe_native_process_image")
        return image_inventory(base, image, role=role)

    def _paths(self, root, admission):
        operation = operation_path(root, admission)
        inputs = {
            entry.destination: str(
                operation / "inputs" / staged_name(entry.destination)
            )
            for entry in read_manifest(operation, self._launch(operation)).files
        }
        return operation, inputs

    def _launch(self, operation):
        return NativeLaunch.model_validate_json(
            canonical(read_record(operation / "control" / "launch.json"))
        )

    def _verify(self, state, launch, inputs, inventory):
        admission = launch.intent.admission
        role = "native"
        labels = state.get("Config", {}).get("Labels") or {}
        if (
            state.get("Name") != "/" + launch.runtime_name
            or state.get("Image") != admission.image_id
            or labels.get("io.mineagent.oe-native.admission") != str(admission.id)
            or labels.get("io.mineagent.oe-native.launch")
            != digest(launch.model_dump(mode="json"))
            or state.get("HostConfig", {}).get("RestartPolicy")
            != {"Name": "no", "MaximumRetryCount": 0}
        ):
            raise ProcessError("oe_native_process_runtime_identity")
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

    def execute(self, root, authority, *, on_identity, before_submission=None):
        if type(authority) is not NativeAuthority:
            raise ProcessError("oe_native_process_authority")
        inventory = authority.consume()
        self._inventory = inventory
        self.client.deadline = authority.deadline + 1
        intent = authority.intent
        admission = intent.admission
        if admission.image_id != self.image_id or MAGIC != b"MAOE1\x00":
            raise ProcessError("oe_native_process_runtime_identity")
        root = Path(root).absolute()
        if any(value in str(root) for value in (",", "\n", "\r")):
            raise ProcessError("oe_native_process_root")
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
                launch = NativeLaunch(
                    schema_version="mineagent.openevolve-native-process-launch.v1",
                    intent=intent,
                    runtime_name="mineagent-oe-native-" + str(admission.id),
                    root_device=info.st_dev,
                    root_inode=info.st_ino,
                )
                _write_bytes(
                    operation / "manifest.json",
                    canonical(authority.manifest.model_dump(mode="json")),
                    0o600,
                )
                # Durable binding exists before any Docker mutation, even create.
                write_record(control / "launch.json", launch.model_dump(mode="json"))
                inputs = {}
                for destination, content in authority.inputs.items():
                    path = source / staged_name(destination)
                    _write_bytes(path, content, 0o444)
                    inputs[destination] = str(path)
                if time.monotonic() >= authority.deadline:
                    raise ProcessError("oe_native_process_deadline")
            if before_submission is not None:
                before_submission()
            with process_fence(root):
                self._verify_staged_launch(root, operation, launch)
                if time.monotonic() >= authority.deadline:
                    raise ProcessError("oe_native_process_deadline")
                args = self._create_args(launch, inputs)
                _, raw = self.client._command(args)
                cid = raw.decode("ascii").strip()
                if re.fullmatch(r"[a-f0-9]{64}", cid) is None:
                    raise ProcessError("oe_native_process_runtime_identity")
                self._verify(self.client._inspect(cid), launch, inputs, inventory)
                reader = AttachStream.connect(cid, deadline=authority.deadline)
            if before_submission is not None:
                before_submission()
            with process_fence(root):
                self._verify_staged_launch(root, operation, launch)
                self._verify(self.client._inspect(cid), launch, inputs, inventory)
                if (
                    time.monotonic() >= authority.deadline
                    or datetime.now(UTC) >= intent.not_after
                ):
                    raise ProcessError("oe_native_process_deadline")
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
            kind, payload = read_native_frame(reader, authority.deadline)
            require_live_native_frame(
                self.client._inspect(cid), time.monotonic(), authority.deadline
            )
            basename = "native-export.bin"
            if kind != 2:
                raise ProcessError("oe_native_process_output_rejected")
            validate_export(payload)
            content = payload
            # Do not persist any successful extraction until the channel reaches
            # EOF with no trailing/duplicate bytes under the original unit deadline.
            require_complete_native_stream(reader, authority.deadline + 1)
            while True:
                stopped = self.client._inspect(cid)
                if stopped is None or time.monotonic() > authority.deadline + 1:
                    raise ProcessError("oe_native_process_original_deadline")
                if stopped["State"]["Running"] is False:
                    if stopped["State"]["ExitCode"] != 124:
                        raise ProcessError("oe_native_process_original_deadline")
                    break
                time.sleep(0.01)
            extraction = NativeExtraction(
                launch_sha256=digest(launch.model_dump(mode="json")),
                identity_sha256=digest(identity.model_dump(mode="json")),
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                manifest_sha256=intent.inputs.manifest_sha256,
                native_artifacts_sha256=intent.inputs.native_artifacts_sha256,
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
                    raise ProcessError("oe_native_process_original_deadline") from None

    def _verify_staged_launch(self, root, operation, launch):
        if self._launch(operation) != launch:
            raise ProcessError("oe_native_process_journal_identity")
        fd = _directory(root)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
                raise ProcessError("oe_native_process_root_identity")
        finally:
            os.close(fd)
        self._remove_inputs(operation, launch, verify_only=True)

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
            "io.mineagent.oe-native.admission=" + str(launch.intent.admission.id),
            "--label",
            "io.mineagent.oe-native.launch=" + digest(launch.model_dump(mode="json")),
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
            "1g",
            "--memory-swap",
            "1g",
            "--pids-limit",
            "64",
            "--cpus",
            "1",
            "--user",
            "0:0",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65533,gid=65533,mode=0700",
            "--tmpfs",
            "/output:rw,noexec,nosuid,nodev,size=8m,uid=0,gid=0,mode=0733",
            "--tmpfs",
            "/status:rw,noexec,nosuid,nodev,size=20m,uid=0,gid=0,mode=0700",
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
            raise ProcessError("oe_native_process_identity_unobserved")
        pid = state["State"]["Pid"]
        from runtimes.openevolve_evaluator.daemon_identity import observe_leader

        ticks, namespace, boot = observe_leader(
            state["Id"], self.image_id, pid, self.client.deadline
        )
        return NativeIdentity(
            schema_version="mineagent.openevolve-native-process-identity.v1",
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
            raise ProcessError("oe_native_process_uncertain")
        previous_deadline = self.client.deadline
        self.client.deadline = budget
        try:
            with process_fence(root):
                operation, inputs = self._paths(root, admission)
                launch = self._launch(operation)
                if launch.intent.admission != admission:
                    raise ProcessError("oe_native_process_journal_identity")
                root_fd = _directory(root)
                try:
                    info = os.fstat(root_fd)
                finally:
                    os.close(root_fd)
                if (info.st_dev, info.st_ino) != (
                    launch.root_device,
                    launch.root_inode,
                ):
                    raise ProcessError("oe_native_process_root_identity")
                stop_path = operation / "control" / "stop.json"
                identity_path = operation / "control" / "identity.json"
                state = self.client._inspect(launch.runtime_name)
                role = "native"
                if state is not None:
                    self._verify(state, launch, inputs, self.verified_inventory(role))
                if identity_path.exists():
                    identity = NativeIdentity.model_validate_json(
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
                                "oe_native_process_identity_unobserved"
                            ) from None
                        write_record(identity_path, identity.model_dump(mode="json"))
                    else:
                        self._cleanup_unobserved(state, launch)
                        created = state["State"].get("Status") == "created" and state[
                            "State"
                        ].get("StartedAt") in (None, "0001-01-01T00:00:00Z")
                        raise ProcessError(
                            "oe_native_process_created_cleanup_uncertain"
                            if created
                            else "oe_native_process_identity_unobserved"
                        )
                else:
                    # Missing start/identity response is NOT proof of no work.
                    # Never invent a fake PID or v1 created-state identity.
                    raise ProcessError("oe_native_process_identity_unobserved")
                if (
                    identity.admission_sha256
                    != digest(admission.model_dump(mode="json"))
                    or identity.launch_sha256 != digest(launch.model_dump(mode="json"))
                    or identity.image_id != admission.image_id
                ):
                    raise ProcessError("oe_native_process_journal_identity")
                if state is not None:
                    if state["Id"] != identity.container_id:
                        raise ProcessError("oe_native_process_runtime_identity")
                    if state["State"]["Running"] is True:
                        from runtimes.openevolve_evaluator.daemon_identity import (
                            observe_leader,
                        )

                        ticks, namespace, boot = observe_leader(
                            state["Id"],
                            self.image_id,
                            state["State"]["Pid"],
                            self.client.deadline,
                        )
                        if (state["State"]["Pid"], ticks, namespace, boot) != (
                            identity.init_pid,
                            identity.init_start_ticks,
                            identity.pid_namespace_inode,
                            identity.boot_id,
                        ):
                            raise ProcessError("oe_native_process_kernel_identity")
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
                    raise ProcessError("oe_native_process_uncertain")
                stop = NativeStop(
                    schema_version="mineagent.openevolve-native-process-stop.v1",
                    launch_sha256=identity.launch_sha256,
                    identity_sha256=digest(identity.model_dump(mode="json")),
                    container_id=identity.container_id,
                    confirmed_absent=True,
                    kernel_identity_absent=True,
                    observed_at=datetime.now(UTC),
                    deadline_bound_met=original_deadline is not None
                    and time.monotonic() <= original_deadline,
                )
                validate_native_journal(launch, identity, stop)
                if stop_path.exists():
                    existing = NativeStop.model_validate_json(
                        canonical(read_record(stop_path))
                    )
                    validate_native_journal(launch, identity, existing)
                    self._remove_inputs(operation, launch, allow_missing=True)
                    return existing
                # Validate all mounted bytes before making the durable stop/input
                # verification checkpoint; cleanup can then resume after a crash.
                self._remove_inputs(operation, launch, verify_only=True)
                if original_deadline is not None and (
                    time.monotonic() > original_deadline or not stop.deadline_bound_met
                ):
                    raise ProcessError("oe_native_process_original_deadline")
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
            raise ProcessError("oe_native_process_runtime_identity")
        self.client._command(["rm", "--force", cid])
        if (
            self.client._inspect(cid) is not None
            or self.client._inspect(launch.runtime_name) is not None
        ):
            raise ProcessError("oe_native_process_uncertain")

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
                staged_name(item.destination): item
                for item in read_manifest(operation, launch).files
            }
            present = set(os.listdir(directory))
            if not present <= set(expected) or (
                not allow_missing and present != set(expected)
            ):
                raise ProcessError("oe_native_process_input")
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
                        raise ProcessError("oe_native_process_input")
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
                        raise ProcessError("oe_native_process_input")
                    if (
                        len(content) != item.size_bytes
                        or hashlib.sha256(content).hexdigest() != item.sha256
                    ):
                        raise ProcessError("oe_native_process_input")
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
