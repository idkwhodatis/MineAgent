"""Protected RD-v2 host journal grammar/reader, never a launch authority.

Only the trusted host observer writes these files after actual kernel/Docker and
SO_PEERCRED observations. Deserializing a caller DTO cannot authorize a launch.
Stopped-without-child is valid containment evidence, never a successful RPC peer.
"""

import hashlib
import os
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    SHA,
    Closed,
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.rdagent_operational.peer import KernelIdentity, TrustedControllerBinding
from runtimes.rdagent_operational.trusted_report import report_bytes
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import _directory, operation_path

PROFILE = "mineagent.rdagent-orchestrator.v2"
STORAGE_FLOOR = 67_174_400
BOOTSTRAP_CAP = 262144


def controller_admission(value):
    value = ProcessDeclarationV2.model_validate(value)
    if (
        value.role != "rd_orchestrator"
        or value.profile != PROFILE
        or type(value.ipc) is not HostTicketIPC
    ):
        raise ProcessError("rd_controller_profile")
    return value


class ControllerIntent(Closed):
    schema_version: Literal["mineagent.rd-controller-intent.v1"]
    id: UUID
    admission: ProcessDeclarationV2
    bootstrap_sha256: SHA
    bootstrap_size_bytes: int = Field(gt=0, le=BOOTSTRAP_CAP)
    input_layout: Literal["single_bootstrap_json_no_other_mounts"]
    issued_at: datetime
    not_after: datetime

    @model_validator(mode="after")
    def exact(self):
        admission = controller_admission(self.admission)
        if admission.input_sha256 != self.bootstrap_sha256:
            raise ValueError("Bootstrap commitment differs")
        if any(
            t.tzinfo is None or t.utcoffset() != UTC.utcoffset(t)
            for t in (self.issued_at, self.not_after)
        ):
            raise ValueError("UTC deadline required")
        if not 0 < (self.not_after - self.issued_at).total_seconds() <= 60:
            raise ValueError("Original controller deadline exceeds profile")
        return self


class ControllerLaunch(Closed):
    schema_version: Literal["mineagent.rd-controller-launch.v1"]
    intent: ControllerIntent
    runtime_name: str
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)

    @model_validator(mode="after")
    def exact(self):
        if self.runtime_name != "mineagent-rd-controller-" + str(
            self.intent.admission.id
        ):
            raise ValueError("Runtime name differs")
        return self


class ControllerIdentity(Closed):
    schema_version: Literal["mineagent.rd-controller-identity.v1"]
    admission_sha256: SHA
    launch_sha256: SHA
    container_id: SHA
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    root_device: int = Field(ge=0)
    root_inode: int = Field(gt=0)
    boot_id: UUID
    leader: KernelIdentity

    @model_validator(mode="after")
    def root(self):
        if self.leader.uids != (0,) * 4 or self.leader.gids != (0,) * 4:
            raise ValueError("Protected leader requires root identity")
        return self


class ControllerChild(Closed):
    schema_version: Literal["mineagent.rd-controller-child.v1"]
    identity_sha256: SHA
    admission_sha256: SHA
    controller: KernelIdentity
    namespace_pids: tuple[int, ...] = Field(min_length=2, max_length=16)
    namespace_pid: int = Field(gt=1)
    started_report_sha256: SHA
    peer_pid: int = Field(gt=0)
    peer_uid: Literal[65533]
    peer_gid: Literal[65533]

    @model_validator(mode="after")
    def actual_mapping(self):
        if any(type(pid) is not int or pid <= 0 for pid in self.namespace_pids):
            raise ValueError("Exact observed NSpid required")
        if (
            self.namespace_pids[0] != self.controller.host_pid
            or self.namespace_pids[-1] != self.namespace_pid
            or self.peer_pid != self.controller.host_pid
        ):
            raise ValueError("SO_PEERCRED/NSpid mapping differs")
        expected = report_bytes(
            "started", self.namespace_pid, self.controller.start_ticks
        )
        if hashlib.sha256(expected).hexdigest() != self.started_report_sha256:
            raise ValueError("Trusted started report differs")
        return self


class ControllerStop(Closed):
    schema_version: Literal["mineagent.rd-controller-stop.v1"]
    launch_sha256: SHA
    identity_sha256: SHA
    child_sha256: SHA | None
    container_id: SHA
    confirmed_absent: Literal[True]
    kernel_identity_absent: Literal[True]
    child_kernel_identity_absent: Literal[True] | None

    @field_validator(
        "confirmed_absent",
        "kernel_identity_absent",
        "child_kernel_identity_absent",
        mode="before",
    )
    @classmethod
    def exact_boolean(cls, value):
        if value is not None and type(value) is not bool:
            raise ValueError("Exact stop boolean required")
        return value

    @model_validator(mode="after")
    def missing_child_not_invented(self):
        if (self.child_sha256 is None) != (self.child_kernel_identity_absent is None):
            raise ValueError("Unknown child cannot acquire invented stop evidence")
        return self


def validate_records(launch, identity, child=None, stop=None):
    launch = ControllerLaunch.model_validate(launch)
    identity = ControllerIdentity.model_validate(identity)
    admission = launch.intent.admission
    identity_hash = digest(identity.model_dump(mode="json"))
    if (
        identity.launch_sha256 != digest(launch.model_dump(mode="json"))
        or identity.admission_sha256 != digest(admission.model_dump(mode="json"))
        or identity.image_id != admission.image_id
        or (identity.root_device, identity.root_inode)
        != (launch.root_device, launch.root_inode)
    ):
        raise ProcessError("rd_controller_journal_identity")
    if child is not None:
        child = ControllerChild.model_validate(child)
        if (
            child.identity_sha256 != identity_hash
            or child.admission_sha256 != identity.admission_sha256
        ):
            raise ProcessError("rd_controller_child_identity")
        TrustedControllerBinding(
            identity.admission_sha256, identity.leader, child.controller
        )
    if stop is not None:
        stop = ControllerStop.model_validate(stop)
        if (
            stop.launch_sha256 != identity.launch_sha256
            or stop.identity_sha256 != identity_hash
            or stop.container_id != identity.container_id
            or stop.child_sha256
            != (None if child is None else digest(child.model_dump(mode="json")))
        ):
            raise ProcessError("rd_controller_stop_identity")
    return launch, identity, child, stop


def read_controller_journal(root, admission, *, with_child=False, with_stop=False):
    """Caller retains lifecycle admission; all control reads nofollow/bounded16KiB.

    Child is optional only for leader discovery/bootstrap-failure containment.
    Caller must request with_child=True before granting any controller event.
    """
    admission = controller_admission(admission)
    directory = operation_path(root, admission) / "control"

    def read(name, cls):
        return cls.model_validate_json(canonical(read_numeric_record(directory / name)))

    launch = read("launch.json", ControllerLaunch)
    identity = read("identity.json", ControllerIdentity)
    stop = read("stop.json", ControllerStop) if with_stop else None
    if stop is not None and stop.child_sha256 is not None:
        with_child = True
    if stop is not None and stop.child_sha256 is None:
        try:
            read_numeric_record(directory / "child.json")
        except FileNotFoundError:
            pass
        else:
            raise ProcessError("rd_controller_stop_omits_retained_child")
    child = read("child.json", ControllerChild) if with_child else None
    if launch.intent.admission != admission:
        raise ProcessError("rd_controller_journal_admission")
    fd = _directory(root)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
            raise ProcessError("rd_controller_root_identity")
    finally:
        os.close(fd)
    return validate_records(launch, identity, child, stop)
