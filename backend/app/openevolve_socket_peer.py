"""Resolve actual Unix peers from prior durable scheduler placement, never arrival.

The protected controller binding/context are host-owned launch observations.
Every returned peer is checked against persisted controller receipts under the
current fence. A worker can wait briefly for its concurrent controller placement,
but waiting never holds a database transaction or nominates a worker identity.
"""

import hashlib
import socket
import struct
import time
from uuid import UUID

from sqlalchemy import select

from backend.app.openevolve_controller_events import _ticket
from backend.app.openevolve_controller_services import (
    require_search_controller,
    require_search_worker_unit,
)
from backend.app.openevolve_services import _owned_row, _scope
from backend.app.openevolve_worker_models import (
    SearchWorkerExpectedIdentity,
    SearchWorkerTicket,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.openevolve_operational.peer import (
    ControllerBinding,
    WorkerBinding,
    authenticate,
)


def authenticate_socket(
    factory,
    fence,
    channel,
    *,
    admission,
    controller_binding,
    context,
    epoch_id,
    process_ticket,
    ticket_key,
):
    if (
        not isinstance(channel, socket.socket)
        or channel.family != socket.AF_UNIX
        or type(controller_binding) is not ControllerBinding
        or type(context) is not DaemonIdentityContext
        or type(epoch_id) is not UUID
        or type(admission) is not ProcessDeclarationV2
        or type(admission.ipc) is not HostTicketIPC
        or type(process_ticket) is not str
        or len(process_ticket) != 64
        or hashlib.sha256(process_ticket.encode()).hexdigest()
        != admission.ipc.ticket_sha256
        or type(ticket_key) is not bytes
        or len(ticket_key) != 32
    ):
        raise ValueError("Fixed actual Unix controller context required")
    from backend.app.openevolve_lock_admission import acquisition_window

    placement_deadline = min(context.deadline, time.monotonic() + 5.0)
    with acquisition_window(fence, placement_deadline):
        return _authenticate_socket(
            factory,
            fence,
            channel,
            admission=admission,
            controller_binding=controller_binding,
            context=context,
            epoch_id=epoch_id,
            process_ticket=process_ticket,
            ticket_key=ticket_key,
            placement_deadline=placement_deadline,
        )


def _authenticate_socket(
    factory,
    fence,
    channel,
    *,
    admission,
    controller_binding,
    context,
    epoch_id,
    process_ticket,
    ticket_key,
    placement_deadline,
):
    pid, uid, gid = struct.unpack(
        "3i",
        channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        ),
    )
    if uid != 65533 or gid != 65533 or pid <= 0:
        raise ValueError("Protected fixed child peer required")
    if pid == controller_binding.controller.host_pid:
        peer = authenticate(
            channel,
            admission,
            binding=controller_binding,
            context=context,
            process_ticket=process_ticket,
        )
        with neutral_transaction(factory, fence) as session:
            require_search_controller(session, fence, peer, epoch_id=epoch_id)
        return peer
    # A spawned worker can connect before the controller's placement RPC has
    # committed. Only an independently observed persisted row ends this wait.
    while time.monotonic() < placement_deadline:
        with neutral_transaction(factory, fence) as session:
            rows = session.scalars(
                select(SearchWorkerExpectedIdentity)
                .filter_by(
                    **_scope(fence),
                    epoch_id=epoch_id,
                    admission_id=admission.id,
                    host_pid=pid,
                )
                .limit(3)
            ).all()
            if len(rows) > 2:
                raise ValueError("Initial worker placement bound exceeded")
            # One pool worker can serve both serial intents; only the latest
            # immutable assignment is current, not the first PID match.
            candidates = []
            from backend.app.openevolve_models import SearchIterationIntent
            from backend.app.openevolve_worker_models import (
                SearchWorkerAssignmentClosure,
            )

            for row in rows:
                intent = _owned_row(
                    session, SearchIterationIntent, row.intent_id, fence
                )
                closed = session.scalar(
                    select(SearchWorkerAssignmentClosure).filter_by(intent_id=intent.id)
                )
                if closed is None:
                    candidates.append((row, intent))
            if len(candidates) > 1:
                raise ValueError("Concurrent assignment for one exact worker")
            if candidates:
                row, intent = candidates[0]
                ticket = _owned_row(session, SearchWorkerTicket, row.ticket_id, fence)
                if (ticket.epoch_id, ticket.admission_id, ticket.intent_id) != (
                    epoch_id,
                    admission.id,
                    row.intent_id,
                ):
                    raise ValueError("Worker ticket placement scope differs")
                expected = (
                    row.host_pid,
                    row.start_ticks,
                    row.pid_namespace,
                    row.cgroup,
                )
                intent_id, ticket_sha = intent.id, ticket.ticket_sha256
            else:
                expected = None
        if expected is not None:
            worker = context.observe(pid)
            if (
                tuple(
                    getattr(worker, k)
                    for k in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
                )
                != expected
            ):
                raise ValueError("Current kernel worker differs from durable placement")
            worker_ticket = _ticket(ticket_key, epoch_id, intent_id)
            if hashlib.sha256(worker_ticket.encode()).hexdigest() != ticket_sha:
                raise ValueError("Host key does not match durable worker ticket")
            binding = WorkerBinding(
                controller_binding, epoch_id, intent_id, ticket_sha, worker
            )
            peer = authenticate(
                channel,
                admission,
                binding=binding,
                context=context,
                process_ticket=process_ticket,
                worker_ticket=worker_ticket,
            )
            with neutral_transaction(factory, fence) as session:
                require_search_worker_unit(
                    session, fence, peer, epoch_id=epoch_id, intent_id=intent_id
                )
            return peer
        time.sleep(min(0.05, max(0, placement_deadline - time.monotonic())))
    raise ValueError("No independently committed worker placement before deadline")
