"""Unix IPC authority requires observed kernel membership AND an exact ticket."""

import hashlib
import hmac
import os
import socket
import struct
from dataclasses import dataclass
from types import MappingProxyType

from packages.research_runtime.process_contracts import (
    ProcessAdmission,
    ProcessError,
    digest,
)
from runtimes.research_supervisor.supervisor import process_identity

_ISSUER = object()


@dataclass(frozen=True)
class AuthenticatedPeer:
    admission: ProcessAdmission
    leader: dict
    peer: dict
    _issuer: object

    def __post_init__(self):
        object.__setattr__(self, "leader", MappingProxyType(dict(self.leader)))
        object.__setattr__(self, "peer", MappingProxyType(dict(self.peer)))

    @property
    def admission_id(self):
        return self.admission.id

    @property
    def logical_operation_id(self):
        return self.admission.logical_operation_id


def _revalidate(authority):
    if type(authority) is not AuthenticatedPeer or authority._issuer is not _ISSUER:
        raise ProcessError("research_process_peer")
    try:
        if (
            process_identity(authority.leader["host_pid"]) != authority.leader
            or process_identity(authority.peer["host_pid"]) != authority.peer
        ):
            raise ProcessError("research_process_peer")
    except (OSError, ValueError, KeyError, TypeError):
        raise ProcessError("research_process_peer") from None


def require_authenticated_peer(authority, fence, *, logical_operation_id=None):
    _revalidate(authority)
    admission = authority.admission
    if (
        (
            admission.owner_id,
            admission.run_id,
            admission.attempt_id,
            admission.generation,
        )
        != (fence.owner_id, fence.run_id, fence.attempt_id, fence.generation)
        or logical_operation_id is not None
        and admission.logical_operation_id != logical_operation_id
    ):
        raise ProcessError("research_process_peer")
    return authority


def authenticate_peer(peer_socket, admission, observed_identity, supplied_ticket):
    if (
        type(admission) is not ProcessAdmission
        or type(supplied_ticket) is not str
        or len(supplied_ticket) != 64
        or not hmac.compare_digest(
            hashlib.sha256(supplied_ticket.encode()).hexdigest(),
            admission.ticket_sha256,
        )
    ):
        raise ProcessError("research_process_peer")
    try:
        if observed_identity.get("admission_sha256") != digest(admission.public()):
            raise ProcessError("research_process_peer")
        pid, uid, _ = struct.unpack(
            "3i",
            peer_socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )
        leader = process_identity(observed_identity["host_pid"])
        if observed_identity.get("observed_state") != "running" or any(
            leader[key] != observed_identity[key] for key in leader
        ):
            raise ProcessError("research_process_peer")
        actual = process_identity(pid)
        if uid != os.getuid() or any(
            actual[key] != leader[key] for key in ("pid_namespace", "cgroup")
        ):
            raise ProcessError("research_process_peer")
        return AuthenticatedPeer(admission, leader, actual, _ISSUER)
    except (OSError, ValueError, KeyError, TypeError):
        raise ProcessError("research_process_peer") from None


def accept_fixture_peer(storage_root, admission, observed_identity, *, seconds=10):
    """Bounded N1 fixture handshake; self-reported PID is never authority."""
    import json
    import time

    from runtimes.research_supervisor.supervisor import _directory, operation_path

    if type(seconds) not in (int, float) or not 0 < seconds <= 10:
        raise ProcessError("research_process_peer")
    deadline = time.monotonic() + seconds
    ipc_fd = _directory(operation_path(storage_root, admission) / "ipc")
    try:
        with socket.socket(socket.AF_UNIX) as listener:
            # Linux AF_UNIX sockaddr length is bounded regardless of storage path.
            listener.bind(f"/proc/self/fd/{ipc_fd}/peer.sock")
            listener.listen(1)
            listener.settimeout(max(0.001, deadline - time.monotonic()))
            peer, _ = listener.accept()
            with peer:
                data = b""
                while not data.endswith(b"\n"):
                    if len(data) >= 4096 or time.monotonic() >= deadline:
                        raise ProcessError("research_process_peer")
                    peer.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = peer.recv(4096 - len(data))
                    if not chunk:
                        raise ProcessError("research_process_peer")
                    data += chunk
                payload = json.loads(data)
                if type(payload) is not dict or set(payload) != {
                    "ticket",
                    "controller_exited",
                    "pool_alive",
                    "pid",
                }:
                    raise ProcessError("research_process_peer")
                if (
                    payload["controller_exited"] is not True
                    or payload["pool_alive"] is not True
                ):
                    raise ProcessError("research_process_peer")
                return authenticate_peer(
                    peer, admission, observed_identity, payload["ticket"]
                )
    except (OSError, ValueError, TypeError):
        raise ProcessError("research_process_peer") from None
    finally:
        os.close(ipc_fd)
