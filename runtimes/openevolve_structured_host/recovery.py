"""Exact-unit containment recovery, never controller launch or result authority.

Explicit normal027 journal grammar; no old024 codec or admission reuse.
Caller retains shared maintenance admission across this operation. A missing
kernel observation never becomes a synthetic PID/positive-stop receipt. Exact
unobserved Docker units may be removed, while their input journals and budget
remain unresolved. No deadline extension is granted to a controller.
"""

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.openevolve_structured_host.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerLaunch,
    ControllerStop,
    controller_admission,
    validate_records,
)
from runtimes.openevolve_structured_host.supervisor import (
    RunningController,
    _kernel_gone,
    verify_container,
)
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import (
    _directory,
    operation_path,
    process_fence,
    write_record,
)


def _read(path, model):
    return model.model_validate_json(canonical(read_numeric_record(path)))


def _optional(path, model):
    try:
        return _read(path, model)
    except FileNotFoundError:
        return None


def recover_controller(supervisor, root, admission):
    """Stop only this journal-bound unit; do not settle or import any output."""
    admission = controller_admission(admission)
    if admission.image_id != supervisor.image_id:
        raise ProcessError("oe_structured_controller_recovery_image")
    root = Path(root).absolute()
    operation = operation_path(root, admission)
    control = operation / "control"
    # Observer/kernel-wait budget, not a renewed execution deadline or total
    # wall-time promise: each shared Docker CLI call has its own 10-second cap.
    recovery_deadline = time.monotonic() + 30
    with process_fence(root):
        launch = _read(control / "launch.json", ControllerLaunch)
        if launch.intent.admission != admission:
            raise ProcessError("oe_structured_controller_recovery_admission")
        fd = _directory(root)
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
            raise ProcessError("oe_structured_controller_recovery_root")
        identity = _optional(control / "identity.json", ControllerIdentity)
        child = _optional(control / "child.json", ControllerChild)
        previous = _optional(control / "stop.json", ControllerStop)
        state = supervisor.cli._inspect(launch.runtime_name)
        if state is not None:
            verify_container(state, launch, operation, supervisor.inventory)
        if identity is None:
            if child is not None or previous is not None:
                raise ProcessError("oe_structured_controller_recovery_predecessor")
            if state is None:
                raise ProcessError("oe_structured_controller_identity_unobserved")
            cid = state["Id"]
            if state["State"]["Running"] is True:
                try:
                    leader = DaemonIdentityContext(
                        cid,
                        admission.image_id,
                        state["State"]["Pid"],
                        recovery_deadline,
                    ).observe(state["State"]["Pid"])
                    identity = ControllerIdentity(
                        schema_version="mineagent.oe-structured-controller-identity.v1",
                        admission_sha256=digest(admission.model_dump(mode="json")),
                        launch_sha256=digest(launch.model_dump(mode="json")),
                        container_id=cid,
                        image_id=admission.image_id,
                        root_device=launch.root_device,
                        root_inode=launch.root_inode,
                        boot_id=UUID(
                            Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                        ),
                        leader=leader,
                    )
                except (OSError, ValueError, ProcessError):
                    _remove_unobserved(supervisor, launch, state)
                    raise ProcessError(
                        "oe_structured_controller_identity_unobserved"
                    ) from None
                write_record(
                    control / "identity.json", identity.model_dump(mode="json")
                )
            else:
                _remove_unobserved(supervisor, launch, state)
                raise ProcessError("oe_structured_controller_identity_unobserved")
        validate_records(launch, identity, child, previous)
        # Preserve an existing success observation, but never create/import one
        # from mere absence or from recovery-time daemon exit metadata.
        from runtimes.openevolve_structured_host.completion_journal import (
            ControllerCompletion,
            validate_completion,
        )

        completion = _optional(control / "completion.json", ControllerCompletion)
        if completion is not None:
            validate_completion(completion, launch, identity, child, previous)
        current_boot = UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        if state is not None:
            if state["Id"] != identity.container_id:
                raise ProcessError(
                    "oe_structured_controller_recovery_container_changed"
                )
            if state["State"]["Running"] is True:
                if identity.boot_id != current_boot:
                    raise ProcessError("oe_structured_controller_recovery_restarted")
                if state["State"]["Pid"] != identity.leader.host_pid:
                    raise ProcessError(
                        "oe_structured_controller_recovery_kernel_changed"
                    )
                if not _kernel_gone(identity.leader):
                    live_child = child is not None and not _kernel_gone(
                        child.controller
                    )
                    pair = DaemonIdentityContext(
                        identity.container_id,
                        identity.image_id,
                        identity.leader.host_pid,
                        recovery_deadline,
                    ).observe_pair(child.controller.host_pid if live_child else None)
                    if pair[0] != identity.leader or (
                        live_child and pair[1] != child.controller
                    ):
                        raise ProcessError(
                            "oe_structured_controller_recovery_kernel_changed"
                        )
            supervisor.cli._command(["rm", "--force", identity.container_id])
        kernel_deadline = min(recovery_deadline, time.monotonic() + 2)

        def absent():
            return identity.boot_id != current_boot or (
                _kernel_gone(identity.leader)
                and (child is None or _kernel_gone(child.controller))
            )

        while not absent() and time.monotonic() < kernel_deadline:
            time.sleep(0.01)
        if (
            supervisor.cli._inspect(identity.container_id) is not None
            or supervisor.cli._inspect(launch.runtime_name) is not None
            or not absent()
        ):
            raise ProcessError("oe_structured_controller_recovery_stop_uncertain")
        stop = ControllerStop(
            schema_version="mineagent.oe-structured-controller-stop.v1",
            launch_sha256=identity.launch_sha256,
            identity_sha256=digest(identity.model_dump(mode="json")),
            child_sha256=None
            if child is None
            else digest(child.model_dump(mode="json")),
            container_id=identity.container_id,
            confirmed_absent=True,
            kernel_identity_absent=True,
            child_kernel_identity_absent=None if child is None else True,
        )
        validate_records(launch, identity, child, stop)
        if previous is not None and previous != stop:
            raise ProcessError("oe_structured_controller_recovery_stop_drift")
        if previous is None:
            write_record(control / "stop.json", stop.model_dump(mode="json"))
        # Same exact fixed-layout cleanup as normal positive stop, idempotent on
        # already removed inputs. No data from the child is parsed or imported.
        RunningController._cleanup(
            SimpleNamespace(root=root, launch=launch, operation=operation)
        )
        return stop


def _remove_unobserved(supervisor, launch, state):
    """Already fully verified exact Docker unit only; retain all host evidence."""
    cid = state.get("Id")
    if type(cid) is not str or re.fullmatch(r"[a-f0-9]{64}", cid) is None:
        raise ProcessError("oe_structured_controller_recovery_container_changed")
    supervisor.cli._command(["rm", "--force", cid])
    if (
        supervisor.cli._inspect(cid) is not None
        or supervisor.cli._inspect(launch.runtime_name) is not None
    ):
        raise ProcessError("oe_structured_controller_recovery_stop_uncertain")
