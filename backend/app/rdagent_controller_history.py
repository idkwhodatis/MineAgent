"""Relocatable retained controller bytes, not a launch or live peer capability.

Caller first proves owner/run/attempt, exact receipt membership and settlement.
Original root device/inode stays immutable evidence and is not rebound to the
restored storage tree. The live reader's inode checks are never relaxed.
"""

import os

from backend.app.core.atomic_storage import _open_directory
from backend.app.factor_native_io import require_native_storage_guard
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.rdagent_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerLaunch,
    ControllerStop,
    validate_records,
)
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import operation_path


def _decode(cls, value):
    if isinstance(value, cls):
        value = value.model_dump(mode="json")
    return cls.model_validate_json(canonical(value))


def read_historical_controller_records(root, *, intent, identity, child, stop):
    """Completed-child path only; incomplete bootstrap cannot become success."""
    require_native_storage_guard(root)
    intent = _decode(ControllerIntent, intent)
    identity = _decode(ControllerIdentity, identity)
    child = _decode(ControllerChild, child)
    stop = _decode(ControllerStop, stop)
    directory = operation_path(root, intent.admission) / "control"
    fd = _open_directory(directory)
    try:
        before = os.fstat(fd)
        names = {"launch.json", "identity.json", "child.json", "stop.json"}
        if set(os.listdir(fd)) != names:
            raise ProcessError("controller_history_inventory")
        launch = _decode(
            ControllerLaunch, read_numeric_record(directory / "launch.json")
        )
        if launch.intent != intent:
            raise ProcessError("controller_history_intent")
        for name, expected in (
            ("identity", identity),
            ("child", child),
            ("stop", stop),
        ):
            if read_numeric_record(directory / (name + ".json")) != expected.model_dump(
                mode="json"
            ):
                raise ProcessError("controller_history_receipt")
        result = validate_records(launch, identity, child, stop)
        named = os.stat(directory, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino) or set(
            os.listdir(fd)
        ) != names:
            raise ProcessError("controller_history_changed")
        require_native_storage_guard(root)
        return result
    finally:
        os.close(fd)
