"""Actual socket authentication for a fixed protected OE controller/pool worker.

All bindings come from host-persisted independently observed receipts. The wire
supplies only tickets, never expected PIDs. A ticket cannot nominate the first
arriving worker. Every event reobserves exact leader/controller/worker kernel
identities through the container-bound daemon observer, never /proc ns readlink.
These ephemeral socket objects grant no database fence or domain operation.
"""

import hashlib
import hmac
import socket
import struct
from dataclasses import dataclass, field
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.openevolve_structured.profile import PROFILE
from runtimes.rdagent_operational.peer import KernelIdentity

_ISSUER = object()


def _fail():
    raise ProcessError("oe_structured_protected_peer")


def _sha(value):
    return (
        type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


@dataclass(frozen=True)
class ControllerBinding:
    container_id: str
    admission_sha256: str
    leader: KernelIdentity
    controller: KernelIdentity

    def __post_init__(self):
        if (
            not _sha(self.container_id)
            or not _sha(self.admission_sha256)
            or type(self.leader) is not KernelIdentity
            or type(self.controller) is not KernelIdentity
        ):
            _fail()
        if (
            self.leader.host_pid == self.controller.host_pid
            or self.controller.ppid != self.leader.host_pid
            or self.leader.pid_namespace != self.controller.pid_namespace
            or self.leader.cgroup != self.controller.cgroup
            or self.leader.uids != (0,) * 4
            or self.leader.gids != (0,) * 4
            or self.controller.uids != (65533,) * 4
            or self.controller.gids != (65533,) * 4
        ):
            _fail()


@dataclass(frozen=True)
class WorkerBinding:
    controller: ControllerBinding
    epoch_id: UUID
    intent_id: UUID
    ticket_sha256: str
    worker: KernelIdentity

    def __post_init__(self):
        if (
            type(self.controller) is not ControllerBinding
            or type(self.epoch_id) is not UUID
            or type(self.intent_id) is not UUID
            or not _sha(self.ticket_sha256)
            or type(self.worker) is not KernelIdentity
        ):
            _fail()
        control = self.controller.controller
        if (
            self.worker.host_pid in (control.host_pid, self.controller.leader.host_pid)
            or self.worker.ppid != control.host_pid
            or self.worker.pid_namespace != control.pid_namespace
            or self.worker.cgroup != control.cgroup
            or self.worker.uids != (65533,) * 4
            or self.worker.gids != (65533,) * 4
        ):
            _fail()


@dataclass(frozen=True)
class Peer:
    admission: ProcessDeclarationV2
    binding: ControllerBinding | WorkerBinding
    _context: DaemonIdentityContext = field(repr=False, compare=False)
    _socket: socket.socket = field(repr=False, compare=False)
    _issuer: object = field(repr=False, compare=False)

    def __reduce__(self):
        raise TypeError("Live OE peer cannot be serialized")


def _credentials(channel):
    if (
        not isinstance(channel, socket.socket)
        or channel.family != socket.AF_UNIX
        or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
    ):
        _fail()
    return struct.unpack(
        "3i",
        channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        ),
    )


def _revalidate(peer):
    if type(peer) is not Peer or peer._issuer is not _ISSUER:
        _fail()
    try:
        worker = peer.binding if type(peer.binding) is WorkerBinding else None
        binding = worker.controller if worker else peer.binding
        # Reconstruct frozen bindings as a defense against object.__setattr__.
        binding = ControllerBinding(**binding.__dict__)
        if worker:
            WorkerBinding(**worker.__dict__)
        context = peer._context
        if (
            type(context) is not DaemonIdentityContext
            or context.container_id != binding.container_id
            or context.image_id != peer.admission.image_id
            or context.leader_pid != binding.leader.host_pid
        ):
            _fail()
        pair = context.observe_pair(binding.controller.host_pid)
        if pair != (binding.leader, binding.controller):
            _fail()
        expected = binding.controller
        if worker:
            expected = worker.worker
            if (
                context.observe(expected.host_pid) != expected
                or context.observe_pair(binding.controller.host_pid) != pair
            ):
                _fail()
        if _credentials(peer._socket) != (expected.host_pid, 65533, 65533):
            _fail()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _fail()


def authenticate(
    channel, admission, *, binding, context, process_ticket, worker_ticket=None
):
    """Host supplies typed persisted bindings; context binds exact durable CID."""
    if (
        type(admission) is not ProcessDeclarationV2
        or type(binding) not in (ControllerBinding, WorkerBinding)
        or type(context) is not DaemonIdentityContext
    ):
        _fail()
    try:
        admission = ProcessDeclarationV2.model_validate(admission)
        controller = binding.controller if type(binding) is WorkerBinding else binding
        if (
            admission.role != "openevolve_orchestrator"
            or admission.profile != PROFILE
            or type(admission.ipc) is not HostTicketIPC
            or not _sha(process_ticket)
            or not hmac.compare_digest(
                hashlib.sha256(process_ticket.encode()).hexdigest(),
                admission.ipc.ticket_sha256,
            )
            or controller.admission_sha256
            != hashlib.sha256(declaration_bytes(admission)).hexdigest()
        ):
            _fail()
        if type(binding) is WorkerBinding:
            if not _sha(worker_ticket) or not hmac.compare_digest(
                hashlib.sha256(worker_ticket.encode()).hexdigest(),
                binding.ticket_sha256,
            ):
                _fail()
        elif worker_ticket is not None:
            _fail()
        peer = Peer(admission, binding, context, channel, _ISSUER)
        _revalidate(peer)
        return peer
    except (ValueError, TypeError):
        _fail()


def require_peer(peer, fence, *, admission_id, epoch_id=None, intent_id=None):
    _revalidate(peer)
    admission = peer.admission
    if (
        type(fence.generation) is not int
        or admission.id != admission_id
        or (
            admission.owner_id,
            admission.run_id,
            admission.attempt_id,
            admission.generation,
        )
        != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation)
    ):
        _fail()
    if type(peer.binding) is WorkerBinding:
        if (epoch_id, intent_id) != (peer.binding.epoch_id, peer.binding.intent_id):
            _fail()
    elif intent_id is not None:
        _fail()
    return peer
