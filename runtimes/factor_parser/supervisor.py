"""Fail-closed Docker parser containment and durable unknown-stop barriers.

Caller owns PostgreSQL admission and reserves ALL input/output/control bytes
before invocation. This module never grants DB publication authority. Only the
trusted research worker may access Docker; API processes must not import/run it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import UUID

from packages.factors.parser import MAX_REPORT_BYTES, validate_report
from packages.factors.storage import MAX_OUTPUT_BYTES, verified_file

MANAGED = "mineagent-factor-parser-v1"
MAX_JOURNALS = 10_000
_ID = re.compile(r"[0-9a-f]{64}\Z")


class ParserIsolationError(RuntimeError):
    def __init__(self, code="parser_isolation_unavailable"):
        self.code = code
        super().__init__(code)


def _directory(path):
    path = Path(path).absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        result, descriptor = descriptor, -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def factor_fence(storage_root):
    """Independent shared maintenance and exclusive factor locks, nonblocking."""
    root = Path(storage_root).absolute()
    root_fd = _directory(root)
    factors_fd = None
    try:
        fcntl.flock(root_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        factors_fd = _directory(root / "factors")
        fcntl.flock(factors_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now, pinned = (
            os.stat(root / "factors", follow_symlinks=False),
            os.fstat(factors_fd),
        )
        if (now.st_dev, now.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise ParserIsolationError("fence_changed")
        yield factors_fd
    except BlockingIOError:
        raise ParserIsolationError("factor_busy") from None
    finally:
        if factors_fd is not None:
            os.close(factors_fd)
        os.close(root_fd)


def _write_record(path, value):
    """Immutable durable control record. Caller holds factor fence."""
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(data) > MAX_REPORT_BYTES:
        raise ParserIsolationError("journal_size")
    parent = _directory(Path(path).parent)
    try:
        fd = os.open(
            Path(path).name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def _record(path):
    parent = _directory(Path(path).parent)
    try:
        fd = os.open(
            Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_REPORT_BYTES
        ):
            raise ParserIsolationError("invalid_journal")
        raw = os.read(fd, MAX_REPORT_BYTES + 1)

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        result = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise TypeError()
        return result
    except (ValueError, TypeError, UnicodeError):
        raise ParserIsolationError("invalid_journal") from None
    finally:
        os.close(fd)


def _journal(operation):
    value = _record(Path(operation) / "control" / "launch.json")
    required = {
        "schema_version",
        "owner_id",
        "operation_id",
        "runtime_token",
        "image_id",
        "input_sha256",
        "state",
        "root_device",
        "root_inode",
    }
    if (
        set(value) != required
        or value["schema_version"] != "mineagent.factor-parser-launch.v1"
        or value["state"] != "launch_pending"
    ):
        raise ParserIsolationError("invalid_journal")
    owner = str(UUID(value["owner_id"]))
    op = str(UUID(value["operation_id"]))
    if (
        Path(operation).name != op
        or Path(operation).parent.parent.name != owner
        or value["runtime_token"] != "mineagent-factor-" + op
    ):
        raise ParserIsolationError("invalid_journal")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) or not _ID.fullmatch(
        value["input_sha256"]
    ):
        raise ParserIsolationError("invalid_journal")
    return value


def _preparation_path(operation):
    operation = Path(operation)
    return operation.parent / f"prepared-{operation.name}.json"


def _aborted_path(operation):
    operation = Path(operation)
    return operation.parent / f"aborted-{operation.name}.json"


def _prepared(operation):
    value = _record(_preparation_path(operation))
    required = {
        "schema_version",
        "owner_id",
        "operation_id",
        "runtime_token",
        "image_id",
        "input_sha256",
        "state",
        "root_device",
        "root_inode",
    }
    if (
        set(value) != required
        or value["schema_version"] != "mineagent.factor-parser-prepared.v1"
        or value["state"] != "prepared"
    ):
        raise ParserIsolationError("invalid_preparation")
    if (
        str(UUID(value["operation_id"])) != Path(operation).name
        or str(UUID(value["owner_id"])) != Path(operation).parent.parent.name
        or value["runtime_token"] != "mineagent-factor-" + Path(operation).name
    ):
        raise ParserIsolationError("invalid_preparation")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) or not _ID.fullmatch(
        value["input_sha256"]
    ):
        raise ParserIsolationError("invalid_preparation")
    return value


def prepare_operation(storage_root, operation, *, image_id, input_sha256, fence_fd):
    """Persist intent BEFORE any operation directory/data, under caller's quota fence."""
    root = Path(storage_root).absolute()
    operation = Path(operation).absolute()
    owner = str(UUID(operation.parent.parent.name))
    identity = str(UUID(operation.name))
    if operation != root / "factors" / owner / "staging" / identity:
        raise ParserIsolationError("operation_path")
    info = os.fstat(fence_fd)
    current = (root / "factors").stat()
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise ParserIsolationError("fence_changed")
    if operation.exists() or _preparation_path(operation).exists():
        raise ParserIsolationError("operation_reused")
    # Parent staging directory is installation-owned, no operation bytes yet.
    from backend.app.core.atomic_storage import _prepare_directory

    descriptor = _prepare_directory(operation.parent, root)
    os.close(descriptor)
    record = {
        "schema_version": "mineagent.factor-parser-prepared.v1",
        "owner_id": owner,
        "operation_id": identity,
        "runtime_token": "mineagent-factor-" + identity,
        "image_id": image_id,
        "input_sha256": input_sha256,
        "state": "prepared",
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
    }
    _write_record(_preparation_path(operation), record)
    return record


def _check_aborted(operation, prepared):
    receipt = _record(_aborted_path(operation))
    if receipt != {
        "schema_version": "mineagent.factor-parser-aborted.v1",
        "runtime_token": prepared["runtime_token"],
        "preparation_sha256": hashlib.sha256(
            json.dumps(
                prepared, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest(),
        "never_launched": True,
    }:
        raise ParserIsolationError("invalid_abort_receipt")


def assert_quiescent(storage_root, *, ignore_operation=None):
    """Read under root EX/factor EX; stop evidence is not DB-commit evidence."""
    root = Path(storage_root).absolute() / "factors"
    if root.is_symlink():
        raise ParserIsolationError("invalid_journal_layout")
    if not root.exists():
        return
    deadline = time.monotonic() + 2
    count = 0
    for owner in root.iterdir():
        count += 1
        if (
            count > MAX_JOURNALS
            or time.monotonic() >= deadline
            or owner.is_symlink()
            or not owner.is_dir()
        ):
            raise ParserIsolationError("invalid_journal_layout")
        try:
            UUID(owner.name)
        except ValueError:
            raise ParserIsolationError("invalid_journal_layout") from None
        stage = owner / "staging"
        if not stage.exists():
            continue
        if stage.is_symlink() or not stage.is_dir():
            raise ParserIsolationError("invalid_journal_layout")
        operations = set()
        for entry in stage.iterdir():
            count += 1
            if (
                count > MAX_JOURNALS
                or time.monotonic() >= deadline
                or entry.is_symlink()
            ):
                raise ParserIsolationError("invalid_journal_layout")
            if entry.is_dir():
                try:
                    operations.add(str(UUID(entry.name)))
                except ValueError:
                    raise ParserIsolationError("invalid_journal_layout") from None
            elif entry.is_file():
                match = re.fullmatch(
                    r"(?:prepared|aborted)-([0-9a-f-]{36})\.json", entry.name
                )
                if match is None:
                    raise ParserIsolationError("invalid_journal_layout")
                operations.add(str(UUID(match[1])))
            else:
                raise ParserIsolationError("invalid_journal_layout")
        for identity in operations:
            operation = stage / identity
            if (
                ignore_operation is not None
                and operation == Path(ignore_operation).absolute()
            ):
                continue
            prepared = _prepared(operation)
            launch_path = operation / "control" / "launch.json"
            if not launch_path.exists():
                if launch_path.is_symlink():
                    raise ParserIsolationError("invalid_journal")
                try:
                    _check_aborted(operation, prepared)
                except FileNotFoundError:
                    raise ParserIsolationError("factor_recovery_required") from None
                continue
            launch = _journal(operation)
            if {
                k: v for k, v in launch.items() if k not in {"schema_version", "state"}
            } != {
                k: v
                for k, v in prepared.items()
                if k not in {"schema_version", "state"}
            }:
                raise ParserIsolationError("launch_preparation_mismatch")
            try:
                stopped = _record(operation / "control" / "stopped.json")
            except FileNotFoundError:
                raise ParserIsolationError("factor_recovery_required") from None
            if (
                set(stopped)
                != {
                    "schema_version",
                    "runtime_token",
                    "container_id",
                    "confirmed_absent",
                }
                or stopped["schema_version"] != "mineagent.factor-parser-stop.v1"
                or stopped["runtime_token"] != launch["runtime_token"]
                or stopped["confirmed_absent"] is not True
                or not isinstance(stopped["container_id"], str)
                or not _ID.fullmatch(stopped["container_id"])
            ):
                raise ParserIsolationError("invalid_stop_receipt")


class DockerParser:
    """One-shot launcher; operation UUIDs and journals may never be reused."""

    def __init__(self, *, image_id, docker_binary=None, output_bytes=16 * 1024 * 1024):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ParserIsolationError("unpinned_image")
        if type(output_bytes) is not int or not 1 <= output_bytes <= MAX_OUTPUT_BYTES:
            raise ParserIsolationError("output_budget")
        self.output_bytes = output_bytes
        self.image_id = image_id
        self.binary = docker_binary or shutil.which("docker")
        if not self.binary:
            raise ParserIsolationError("docker_unavailable")
        self.env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        self.prefix = None

    def _configure(self, control):
        config = control / "docker-config"
        config.mkdir(mode=0o700, exist_ok=True)
        target = config / "config.json"
        if target.exists():
            if _record(target) != {}:
                raise ParserIsolationError("docker_config")
        else:
            _write_record(target, {})
        self.prefix = [
            self.binary,
            "--config",
            str(config),
            "--host",
            "unix:///var/run/docker.sock",
        ]

    def _command(self, *args, timeout=2):
        if timeout <= 0:
            raise ParserIsolationError("docker_command_deadline")
        try:
            process = subprocess.Popen(
                [*self.prefix, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                close_fds=True,
            )
        except OSError:
            raise ParserIsolationError("docker_command_uncertain") from None
        outputs = [bytearray(), bytearray()]
        overflow = threading.Event()

        def capture(pipe, index):
            while chunk := pipe.read(4096):
                if len(outputs[index]) + len(chunk) > 65536:
                    overflow.set()
                    return
                outputs[index].extend(chunk)

        threads = [
            threading.Thread(target=capture, args=(process.stdout, 0), daemon=True),
            threading.Thread(target=capture, args=(process.stderr, 1), daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if overflow.is_set() or time.monotonic() >= deadline:
                    raise ParserIsolationError("docker_command_uncertain")
                time.sleep(0.01)
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if overflow.is_set() or any(t.is_alive() for t in threads):
                raise ParserIsolationError("docker_response_limit")
            return subprocess.CompletedProcess(
                args, process.returncode, bytes(outputs[0]), bytes(outputs[1])
            )
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (process.stdout, process.stderr):
                pipe.close()

    def _inspect(self, launch, *, container_id=None, timeout=2):
        target = container_id or launch["runtime_token"]
        result = self._command("inspect", target, timeout=timeout)
        if result.returncode:
            if (
                b"No such object:" in result.stderr
                or b"No such container:" in result.stderr
            ):
                return None
            raise ParserIsolationError("inspect_unknown")
        try:
            values = json.loads(result.stdout)
            if not isinstance(values, list) or len(values) != 1:
                raise ValueError()
            value = values[0]
            labels = value["Config"]["Labels"]
            identity = value["Id"]
            if not _ID.fullmatch(identity) or (
                container_id is not None and identity != container_id
            ):
                raise ValueError()
            if (
                labels.get("io.mineagent.managed") != MANAGED
                or labels.get("io.mineagent.operation") != launch["operation_id"]
                or labels.get("io.mineagent.owner") != launch["owner_id"]
                or value["Image"] != launch["image_id"]
            ):
                raise ValueError()
            if type(value["State"]["Running"]) is not bool:
                raise ValueError()
            return value
        except (KeyError, ValueError, TypeError):
            raise ParserIsolationError("container_identity") from None

    def _stop(self, operation, launch):
        """Known ID absence is proof; unknown-create absence is not."""
        identity = None
        try:
            record = _record(operation / "control" / "container.json")
            if (
                set(record) != {"container_id", "runtime_token"}
                or record["runtime_token"] != launch["runtime_token"]
                or not _ID.fullmatch(record["container_id"])
            ):
                return False
            identity = record["container_id"]
        except FileNotFoundError:
            pass
        try:
            value = self._inspect(launch, container_id=identity)
            if value is None and identity is None:
                return False
            if value is not None:
                identity = value["Id"]
                # Exact immutable ID, never an unverified name, is the mutation target.
                removed = self._command("rm", "--force", identity, timeout=3)
                if removed.returncode:
                    return False
            if self._inspect(launch, container_id=identity) is not None:
                return False
            if self._inspect(launch) is not None:
                return False
            stop = operation / "control" / "stopped.json"
            if not stop.exists():
                _write_record(
                    stop,
                    {
                        "schema_version": "mineagent.factor-parser-stop.v1",
                        "runtime_token": launch["runtime_token"],
                        "container_id": identity,
                        "confirmed_absent": True,
                    },
                )
            return True
        except (ParserIsolationError, OSError, ValueError, TypeError):
            return False

    def reconcile_stop(self, storage_root, operation):
        """Recovery only proves containment. Never infers publication rollback."""
        operation = Path(operation).absolute()
        with factor_fence(storage_root) as fence:
            prepared = _prepared(operation)
            info = os.fstat(fence)
            if (prepared["root_device"], prepared["root_inode"]) != (
                info.st_dev,
                info.st_ino,
            ):
                raise ParserIsolationError("fence_changed")
            launch_path = operation / "control" / "launch.json"
            if not launch_path.exists():
                if launch_path.is_symlink():
                    raise ParserIsolationError("invalid_journal")
                # A durable launch record MUST precede any Docker command.
                receipt = {
                    "schema_version": "mineagent.factor-parser-aborted.v1",
                    "runtime_token": prepared["runtime_token"],
                    "preparation_sha256": hashlib.sha256(
                        json.dumps(
                            prepared,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode()
                    ).hexdigest(),
                    "never_launched": True,
                }
                if not _aborted_path(operation).exists():
                    _write_record(_aborted_path(operation), receipt)
                _check_aborted(operation, prepared)
                return True
            launch = _journal(operation)
            info = os.fstat(fence)
            if (launch["root_device"], launch["root_inode"]) != (
                info.st_dev,
                info.st_ino,
            ):
                raise ParserIsolationError("fence_changed")
            if launch["image_id"] != self.image_id:
                raise ParserIsolationError("image_mismatch")
            self._configure(operation / "control")
            return self._stop(operation, launch)

    def _create_args(self, launch, input_directory):
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:
            raise ParserIsolationError("root_supervisor")
        return [
            "create",
            "--pull=never",
            "--name",
            launch["runtime_token"],
            "--label",
            f"io.mineagent.managed={MANAGED}",
            "--label",
            f"io.mineagent.operation={launch['operation_id']}",
            "--label",
            f"io.mineagent.owner={launch['owner_id']}",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            f"{uid}:{gid}",
            "--cpus=1",
            "--memory=512m",
            "--memory-swap=512m",
            "--pids-limit=32",
            "--ulimit",
            "nofile=64:64",
            "--ulimit",
            "core=0:0",
            "--ulimit",
            f"fsize={self.output_bytes}:{self.output_bytes}",
            "--log-driver=none",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16777216,nr_inodes=128,mode=1777",
            "--tmpfs",
            f"/output:rw,noexec,nosuid,nodev,size={self.output_bytes},nr_inodes=16,mode=0700,uid={uid},gid={gid}",
            "--mount",
            f"type=bind,src={input_directory},dst=/input,readonly",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONHASHSEED=0",
            "--env",
            "OPENBLAS_NUM_THREADS=1",
            "--env",
            "OMP_NUM_THREADS=1",
            self.image_id,
        ]

    def _stream(self, identity, output, deadline):
        """Length-bound report+binary transport, no daemon logs or host RW mount."""
        process = subprocess.Popen(
            [*self.prefix, "start", "--attach", identity],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            close_fds=True,
        )
        errors = []
        reports = []

        def collect():
            try:
                line = process.stdout.readline(MAX_REPORT_BYTES + 1)
                if len(line) > MAX_REPORT_BYTES or not line.endswith(b"\n"):
                    raise ParserIsolationError("parser_report")
                report = validate_report(json.loads(line))
                if report["output_size_bytes"] > self.output_bytes:
                    raise ParserIsolationError("output_budget")
                remaining = report["output_size_bytes"]
                fd = os.open(
                    output / "values.parquet",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                digest = hashlib.sha256()
                try:
                    with os.fdopen(fd, "wb", closefd=False) as target:
                        while remaining:
                            chunk = process.stdout.read(min(65536, remaining))
                            if not chunk:
                                raise ParserIsolationError("truncated_output")
                            target.write(chunk)
                            digest.update(chunk)
                            remaining -= len(chunk)
                        if process.stdout.read(1):
                            raise ParserIsolationError("extra_output")
                        target.flush()
                        os.fsync(fd)
                finally:
                    os.close(fd)
                if digest.hexdigest() != report["output_sha256"]:
                    raise ParserIsolationError("output_hash")
                reports.append(report)
            except BaseException as exc:  # noqa: BLE001 -- propagate worker failure categorically
                errors.append(exc)

        def drain():
            count = 0
            while chunk := process.stderr.read(4096):
                count += len(chunk)
                if count > MAX_REPORT_BYTES:
                    errors.append(ParserIsolationError("stderr_limit"))
                    return

        threads = [
            threading.Thread(target=collect, daemon=True),
            threading.Thread(target=drain, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            while process.poll() is None:
                if errors or time.monotonic() >= deadline:
                    raise ParserIsolationError("parser_deadline")
                time.sleep(0.01)
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if (
                errors
                or any(t.is_alive() for t in threads)
                or process.returncode
                or len(reports) != 1
            ):
                raise ParserIsolationError("parser_failed")
            return reports[0]
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            # Do not release filesystem fencing while a local output writer is
            # still alive (including uninterruptible fsync). Killing the CLI closes
            # its stream; runtime removal remains the caller's separate obligation.
            for thread in threads:
                thread.join()
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()

    def run(self, storage_root, operation, *, input_sha256, deadline, fence_fd=None):
        """Already charged operation/{input,output,control}; caller PG locks first.

        Return only after exact container removal is verified. Input/publication
        DB identities and available_at truth remain caller responsibilities.
        """
        root = Path(storage_root).absolute()
        operation = Path(operation).absolute()
        if not 0 < deadline - time.monotonic() <= 20:
            raise ParserIsolationError("admission_deadline")
        try:
            owner = str(UUID(operation.parent.parent.name))
            op = str(UUID(operation.name))
        except ValueError:
            raise ParserIsolationError("operation_path") from None
        if operation != root / "factors" / owner / "staging" / op:
            raise ParserIsolationError("operation_path")
        if fence_fd is not None:
            current = os.stat(root / "factors", follow_symlinks=False)
            pinned = os.fstat(fence_fd)
            if not stat.S_ISDIR(pinned.st_mode) or (current.st_dev, current.st_ino) != (
                pinned.st_dev,
                pinned.st_ino,
            ):
                raise ParserIsolationError("fence_changed")
        with factor_fence(root) if fence_fd is None else nullcontext(fence_fd) as fence:
            assert_quiescent(root, ignore_operation=operation)
            prepared = _prepared(operation)
            if _aborted_path(operation).exists():
                raise ParserIsolationError("operation_aborted")
            if (
                prepared["image_id"] != self.image_id
                or prepared["input_sha256"] != input_sha256
            ):
                raise ParserIsolationError("preparation_mismatch")
            for name in ("input", "output", "control"):
                descriptor = _directory(operation / name)
                os.close(descriptor)
            if list((operation / "output").iterdir()) or list(
                (operation / "control").iterdir()
            ):
                raise ParserIsolationError("operation_reused")
            if {p.name for p in (operation / "input").iterdir()} not in (
                {"input.parquet"},
                {"input.parquet", "identity-map.json"},
                {"input.parquet", "snapshot-input.json"},
                {"input.parquet", "identity-map.json", "snapshot-input.json"},
            ):
                raise ParserIsolationError("input_inventory")
            with verified_file(
                operation / "input" / "input.parquet",
                input_sha256,
                max_bytes=10_000_000,
            ):
                pass
            info = os.fstat(fence)
            launch = {
                "schema_version": "mineagent.factor-parser-launch.v1",
                "owner_id": owner,
                "operation_id": op,
                "runtime_token": "mineagent-factor-" + op,
                "image_id": self.image_id,
                "input_sha256": input_sha256,
                "state": "launch_pending",
                "root_device": info.st_dev,
                "root_inode": info.st_ino,
            }
            if {
                k: v for k, v in launch.items() if k not in {"schema_version", "state"}
            } != {
                k: v
                for k, v in prepared.items()
                if k not in {"schema_version", "state"}
            }:
                raise ParserIsolationError("preparation_mismatch")
            _write_record(operation / "control" / "launch.json", launch)
            self._configure(operation / "control")
            stopped = False
            try:
                if self._inspect(launch) is not None:
                    raise ParserIsolationError("operation_exists")
                parser_deadline = min(deadline - 3, time.monotonic() + 10)
                if parser_deadline - time.monotonic() < 1:
                    raise ParserIsolationError("admission_deadline")
                created = self._command(
                    *self._create_args(launch, operation / "input"),
                    timeout=parser_deadline - time.monotonic(),
                )
                identity = created.stdout.decode().strip()
                if created.returncode or not _ID.fullmatch(identity):
                    raise ParserIsolationError("create_uncertain")
                _write_record(
                    operation / "control" / "container.json",
                    {
                        "container_id": identity,
                        "runtime_token": launch["runtime_token"],
                    },
                )
                value = self._inspect(launch, container_id=identity)
                if value is None:
                    raise ParserIsolationError("create_missing")
                report = self._stream(identity, operation / "output", parser_deadline)
                state = self._inspect(launch, container_id=identity)
                if (
                    state is None
                    or state["State"]["Running"]
                    or state["State"].get("ExitCode") != 0
                    or state["State"].get("OOMKilled")
                ):
                    raise ParserIsolationError("parser_exit")
                if report["input_sha256"] != input_sha256:
                    raise ParserIsolationError("input_hash")
                from packages.factors.parser import read_identity_map

                mapping_path = operation / "input" / "identity-map.json"
                expected_mapping = (
                    read_identity_map(mapping_path)[1]
                    if mapping_path.exists()
                    else None
                )
                if report["identity_mapping_sha256"] != expected_mapping:
                    raise ParserIsolationError("mapping_hash")
                contract_path = operation / "input" / "snapshot-input.json"
                expected_contract = (
                    hashlib.sha256(contract_path.read_bytes()).hexdigest()
                    if contract_path.exists()
                    else None
                )
                if report["snapshot_input_sha256"] != expected_contract:
                    raise ParserIsolationError("contract_hash")
                stopped = self._stop(operation, launch)
                if not stopped:
                    raise ParserIsolationError("stop_unknown")
                if time.monotonic() >= deadline:
                    raise ParserIsolationError("admission_deadline")
                return report
            finally:
                if not stopped:
                    self._stop(operation, launch)
