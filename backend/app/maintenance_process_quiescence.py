"""Maintenance-only stopped inventory, not execution or result authority.

The two operation callers retain their exclusive storage barrier across this
check and the subsequent mandatory database/physical closure verification.
Restored0400 and original0600 journals are equivalent evidence only here. This
module never starts a process, chmods a file, or relaxes a live runtime reader.
"""

import fcntl
import importlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.cold_journal import _FORMATS
from runtimes.research_supervisor.supervisor import (
    MAX_RECORD,
    MAX_UNITS,
    STOP_SCHEMA,
    _directory,
    digest,
    load_launch,
    validate_identity,
)

_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_uid",
    "st_gid",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _fail():
    raise ProcessError("maintenance_process_integrity")


class _Reader:
    def __init__(self, root, root_fd):
        self.root = Path(root).absolute()
        self.root_fd = root_fd
        self.check_root()

    def check_root(self):
        if type(self.root_fd) is not int or self.root_fd < 0:
            _fail()
        fd = _directory(self.root)
        try:
            a, b = os.fstat(self.root_fd), os.fstat(fd)
            if not stat.S_ISDIR(a.st_mode) or (a.st_dev, a.st_ino) != (
                b.st_dev,
                b.st_ino,
            ):
                _fail()
        finally:
            os.close(fd)

    def raw(self, path, maximum=MAX_RECORD):
        self.check_root()
        path = Path(path).absolute()
        if (
            ".." in path.parts
            or not path.is_relative_to(self.root)
            or path == self.root
        ):
            _fail()
        parent, fd = _directory(path.parent), None
        try:
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
            )
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
                or not 0 < before.st_size <= maximum
            ):
                _fail()
            parts, remaining = [], before.st_size + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                parts.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(parts)
            after = os.fstat(fd)
            linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if len(raw) != before.st_size or any(
                getattr(before, k) != getattr(after, k)
                or getattr(before, k) != getattr(linked, k)
                for k in _FIELDS
            ):
                _fail()
            self.check_root()
            return raw
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def record(self, path):
        def unique(items):
            out = {}
            for k, v in items:
                if k in out:
                    _fail()
                out[k] = v
            return out

        raw = self.raw(path)
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or canonical(value) != raw:
            _fail()
        return value


def _names(path):
    fd = _directory(path)
    try:
        return set(os.listdir(fd))
    finally:
        os.close(fd)


def _validate(operation, schema, reader):
    control = operation / "control"
    if schema == "mineagent.numeric-process-launch.v2":
        from packages.research_runtime.numeric_process_journal import (
            NumericIdentity,
            NumericLaunch,
            NumericStop,
            validate_numeric_journal,
        )

        launch = NumericLaunch.model_validate_json(
            canonical(reader.record(control / "launch.json"))
        )
        identity = NumericIdentity.model_validate_json(
            canonical(reader.record(control / "identity.json"))
        )
        stop = NumericStop.model_validate_json(
            canonical(reader.record(control / "stop.json"))
        )
        admission = launch.intent.admission
        if (str(admission.owner_id), str(admission.id)) != (
            operation.parent.name,
            operation.name,
        ):
            _fail()
        validate_numeric_journal(launch, identity, stop)
        return
    if schema not in _FORMATS:
        # Legacy fixture records already accept owner-only0400. Its exact old
        # launch/identity/stop and closed inventory rules are not reinterpreted.
        launch = load_launch(operation)
        identity = validate_identity(reader.record(control / "identity.json"), launch)
        stop = reader.record(control / "stop.json")
        if stop != {
            "schema_version": STOP_SCHEMA,
            "launch_sha256": digest(launch),
            "container_id": identity["container_id"],
            "confirmed_absent": True,
        }:
            _fail()
        if _names(operation) != {"control"} or _names(control) != {
            "launch.json",
            "identity.json",
            "stop.json",
        }:
            _fail()
        return
    module_name, prefix, validator_name = _FORMATS[schema]
    module = importlib.import_module(module_name)

    def read(name, suffix):
        return getattr(module, prefix + suffix).model_validate_json(
            canonical(reader.record(control / (name + ".json")))
        )

    launch = read("launch", "Launch")
    admission = launch.intent.admission
    if (str(admission.owner_id), str(admission.id)) != (
        operation.parent.name,
        operation.name,
    ):
        _fail()
    identity, stop = read("identity", "Identity"), read("stop", "Stop")
    expected = {"launch.json", "identity.json", "stop.json"}
    if prefix == "Controller":
        child = None
        if stop.child_sha256 is not None:
            child = read("child", "Child")
            expected.add("child.json")
        module.validate_records(launch, identity, child, stop)
        if _names(operation) != {"control"}:
            _fail()
    else:
        extraction = None
        if "extraction.json" in _names(control):
            extraction = read("extraction", "Extraction")
            expected.add("extraction.json")
        getattr(module, validator_name)(launch, identity, stop, extraction)
        expected_names = {"control", "output"}
        if prefix == "Native":
            from runtimes.openevolve_evaluator.contracts import parse_manifest

            launch.intent.inputs.require_manifest(
                parse_manifest(reader.raw(operation / "manifest.json", 65536))
            )
            expected_names.add("manifest.json")
        if _names(operation) != expected_names or _names(operation / "output") != (
            set() if extraction is None else {extraction.basename}
        ):
            _fail()
    if _names(control) != expected:
        _fail()


def assert_maintenance_quiescent(root, *, root_fd):
    """Require the callers' existing pinned exclusive maintenance root descriptor.

    This proves only stopped journal consistency, never DB settlement/ownership
    or valid results. Both callers must still run their complete closure checks.
    """
    try:
        reader = _Reader(root, root_fd)
        namespace = reader.root / "research-processes"
        if not namespace.exists() and not namespace.is_symlink():
            return
        fd = _directory(namespace)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            count = 0
            for owner in os.listdir(fd):
                if str(UUID(owner)) != owner:
                    _fail()
                owner_fd = _directory(namespace / owner)
                try:
                    for unit in os.listdir(owner_fd):
                        count += 1
                        if count > MAX_UNITS or str(UUID(unit)) != unit:
                            _fail()
                        operation = namespace / owner / unit
                        schema = reader.record(
                            operation / "control" / "launch.json"
                        ).get("schema_version")
                        _validate(operation, schema, reader)
                finally:
                    os.close(owner_fd)
            reader.check_root()
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail()
