"""Serial, bounded normal controller event/broker loop on actual owned sockets.

The initial authenticated connection contains the first real event, not a dummy
handshake. Single-worker profile makes synchronous dispatch sufficient; no worker
thread can outlive containment. Return means only the final publication was ACKed,
not that the controller is stopped or the run succeeded. Issuer must still observe
trusted EOF/whole-unit stop and commit the separate terminal closure.
"""

import selectors
import struct
import time

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_controller_process_services import (
    record_controller_child,
)
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_event_wire import read_event
from backend.app.oe_structured_events import apply_event
from backend.app.oe_structured_socket_services import (
    authenticate_event,
    handle_provider_socket,
)
from backend.app.research_lifecycle import neutral_transaction
from runtimes.openevolve_structured_host.supervisor import RunningController


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail("oe_structured_socket_deadline")
    return remaining


def _read_exact(channel, size, deadline):
    chunks = []
    remaining = size
    while remaining:
        channel.settimeout(_remaining(deadline))
        chunk = channel.recv(remaining)
        if not chunk:
            fail("oe_structured_socket_eof")
        chunks.append(chunk)
        remaining -= len(chunk)
    _remaining(deadline)
    return b"".join(chunks)


def read_frame(channel, deadline, limit):
    size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
    if not 0 < size <= limit:
        fail("oe_structured_socket_bound")
    return _read_exact(channel, size, deadline)


def write_frame(channel, raw, deadline):
    if type(raw) is not bytes or not 0 < len(raw) <= 262144:
        fail("oe_structured_socket_reply_bound")
    channel.settimeout(_remaining(deadline))
    channel.sendall(struct.pack("!I", len(raw)) + raw)
    _remaining(deadline)


def _event(factory, fence, epoch_id, settings, channel, running, ticket_key):
    event = read_event(read_frame(channel, running.deadline, 1048576))
    if (
        event.epoch_id != epoch_id
        or event.admission_id != running.launch.intent.admission.id
    ):
        fail("oe_structured_socket_scope")
    peer = authenticate_event(
        factory, fence, event, settings, channel, running=running, ticket_key=ticket_key
    )
    reply = apply_event(
        factory, fence, event, settings, peer=peer, ticket_key=ticket_key
    )
    ready = False
    if event.kind == "checkpoint_publish":
        from backend.app.oe_structured_completion_services import (
            is_final_checkpoint_ready,
        )

        with (
            native_storage_guard(settings.storage_root),
            neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session,
        ):
            ready = is_final_checkpoint_ready(
                session, fence, epoch_id, settings, peer=peer
            )
            if type(ready) is not bool:
                fail("oe_structured_final_publication")
    write_frame(channel, reply, running.deadline)
    return ready


def serve_controller(factory, fence, epoch_id, settings, *, running, ticket_key):
    if (
        type(running) is not RunningController
        or type(ticket_key) is not bytes
        or len(ticket_key) != 32
    ):
        fail("oe_structured_socket_owner")
    if running.child is not None or running.finished or running._stop_started:
        fail("oe_structured_socket_reused")
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):

        def on_child(_record):
            # Callback argument is never DB authority: service rereads protected journal.
            with (
                native_storage_guard(settings.storage_root),
                neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session,
            ):
                record_controller_child(
                    session,
                    fence,
                    running.launch.intent.admission.id,
                    storage_root=settings.storage_root,
                )

        first, _ = running.accept_controller(on_child=on_child)
        with first:
            if _event(factory, fence, epoch_id, settings, first, running, ticket_key):
                fail("oe_structured_first_event_final")
        with selectors.DefaultSelector() as selector:
            selector.register(running.listener, selectors.EVENT_READ, "event")
            selector.register(running.broker_listener, selectors.EVENT_READ, "broker")
            while True:
                selected = selector.select(_remaining(running.deadline))
                if not selected:
                    fail("oe_structured_socket_deadline")
                for key, _ in selected:
                    key.fileobj.settimeout(_remaining(running.deadline))
                    channel, _ = key.fileobj.accept()
                    with channel:
                        if key.data == "event":
                            if _event(
                                factory,
                                fence,
                                epoch_id,
                                settings,
                                channel,
                                running,
                                ticket_key,
                            ):
                                return
                        else:
                            raw = read_frame(channel, running.deadline, 524288)
                            reply = handle_provider_socket(
                                factory,
                                fence,
                                raw,
                                settings,
                                channel,
                                running=running,
                                ticket_key=ticket_key,
                            )
                            write_frame(channel, reply, running.deadline)
