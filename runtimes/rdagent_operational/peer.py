"""RD protected-controller peer checks; no launch, DB, or receipt issuing authority.

The binding must be loaded by trusted host code from its independently observed,
immutable launch receipt, never decoded from a controller message. A successful
check is ephemeral: every event must call require_controller_peer again. This
module does not prove Docker isolation, original deadline, or positive stop.
"""

import hashlib
import hmac
import socket
import struct
from dataclasses import dataclass, field
from pathlib import Path

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.research_supervisor.supervisor import process_identity

PROFILE = "mineagent.rdagent-orchestrator.v2"
LEADER_UID = LEADER_GID = 0
CONTROLLER_UID = CONTROLLER_GID = 65533
_ISSUER = object()


def _fail():
    raise ProcessError("rd_controller_peer")


@dataclass(frozen=True)
class KernelIdentity:
    host_pid: int
    start_ticks: int
    pid_namespace: str
    cgroup: str
    ppid: int
    uids: tuple[int, int, int, int]
    gids: tuple[int, int, int, int]

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in (self.host_pid, self.start_ticks)):
            _fail()
        if type(self.ppid) is not int or self.ppid < 0:
            _fail()
        for values in (self.uids, self.gids):
            if (
                type(values) is not tuple
                or len(values) != 4
                or any(type(v) is not int or v < 0 for v in values)
            ):
                _fail()
        if (
            type(self.pid_namespace) is not str
            or not self.pid_namespace
            or type(self.cgroup) is not str
            or not 0 < len(self.cgroup) <= 4096
        ):
            _fail()


def _status_identity(pid, identity):
    status = (Path("/proc") / str(pid) / "status").read_text()
    fields = dict(line.split(":", 1) for line in status.splitlines())
    if fields["State"].strip().split()[0] in {"Z", "X", "x"}:
        _fail()
    return KernelIdentity(
        **identity,
        ppid=int(fields["PPid"]),
        uids=tuple(map(int, fields["Uid"].split())),
        gids=tuple(map(int, fields["Gid"].split())),
    )


def observe_identity(pid):
    """Host /proc observation; rejects process replacement and zombie evidence."""
    try:
        before = process_identity(pid)
        result = _status_identity(pid, before)
        if _status_identity(pid, before) != result or process_identity(pid) != before:
            _fail()
        return result
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        _fail()


@dataclass(frozen=True)
class TrustedControllerBinding:
    admission_sha256: str
    leader: KernelIdentity
    controller: KernelIdentity

    def __post_init__(self):
        if (
            type(self.admission_sha256) is not str
            or len(self.admission_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.admission_sha256)
            or type(self.leader) is not KernelIdentity
            or type(self.controller) is not KernelIdentity
        ):
            _fail()
        if (
            self.leader.host_pid == self.controller.host_pid
            or self.controller.ppid != self.leader.host_pid
            or self.leader.pid_namespace != self.controller.pid_namespace
            or self.leader.cgroup != self.controller.cgroup
            or self.leader.uids != (LEADER_UID,) * 4
            or self.leader.gids != (LEADER_GID,) * 4
            or self.controller.uids != (CONTROLLER_UID,) * 4
            or self.controller.gids != (CONTROLLER_GID,) * 4
        ):
            _fail()


def _credentials(connection):
    if not isinstance(connection, socket.socket) or connection.family != socket.AF_UNIX:
        _fail()
    if connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
        _fail()
    return struct.unpack(
        "3i",
        connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        ),
    )


@dataclass(frozen=True)
class ControllerPeer:
    admission: ProcessDeclarationV2
    binding: TrustedControllerBinding
    _connection: socket.socket = field(repr=False, compare=False)
    _issuer: object = field(repr=False, compare=False)
    _observer_context: object = field(default=None, repr=False, compare=False)

    def __reduce__(self):
        raise TypeError("Controller peer authority cannot be serialized")


def _revalidate(peer):
    if type(peer) is not ControllerPeer or peer._issuer is not _ISSUER:
        _fail()
    try:
        binding = peer.binding
        observer = observe_identity
        if peer._observer_context is not None:
            from runtimes.rdagent_operational.daemon_identity import (
                DaemonIdentityContext,
            )

            context = peer._observer_context
            if (
                type(context) is not DaemonIdentityContext
                or context.image_id != peer.admission.image_id
                or context.leader_pid != binding.leader.host_pid
            ):
                _fail()
            observed = context.observe_pair(binding.controller.host_pid)
        else:
            observed = (
                observer(binding.leader.host_pid),
                observer(binding.controller.host_pid),
            )
        if _credentials(peer._connection) != (
            binding.controller.host_pid,
            CONTROLLER_UID,
            CONTROLLER_GID,
        ) or observed != (binding.leader, binding.controller):
            _fail()
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        _fail()


def authenticate_controller(
    connection, admission, *, binding, supplied_ticket, observer_context=None
):
    """Only supplied_ticket is request data; admission and binding are host-owned."""
    if (
        type(admission) is not ProcessDeclarationV2
        or type(binding) is not TrustedControllerBinding
    ):
        _fail()
    try:
        admission = ProcessDeclarationV2.model_validate(admission)
        if (
            admission.role != "rd_orchestrator"
            or admission.profile != PROFILE
            or type(admission.ipc) is not HostTicketIPC
            or type(supplied_ticket) is not str
            or len(supplied_ticket) != 64
            or any(c not in "0123456789abcdef" for c in supplied_ticket)
            or not hmac.compare_digest(
                hashlib.sha256(supplied_ticket.encode()).hexdigest(),
                admission.ipc.ticket_sha256,
            )
            or binding.admission_sha256
            != hashlib.sha256(declaration_bytes(admission)).hexdigest()
        ):
            _fail()
        peer = ControllerPeer(admission, binding, connection, _ISSUER, observer_context)
        _revalidate(peer)
        return peer
    except (ValueError, TypeError):
        _fail()


def require_controller_peer(peer, fence, *, logical_operation_id):
    _revalidate(peer)
    admission = peer.admission
    try:
        if type(fence.generation) is not int:
            _fail()
        if (
            admission.owner_id,
            admission.run_id,
            admission.attempt_id,
            admission.generation,
        ) != (
            fence.owner_id,
            fence.run_id,
            fence.attempt_id,
            fence.generation,
        ) or admission.logical_operation_id != logical_operation_id:
            _fail()
    except AttributeError:
        _fail()
    return peer
