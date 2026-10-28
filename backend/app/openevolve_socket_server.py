"""Fixed bounded actual Unix event/broker server for one protected initial epoch.

No arbitrary handler registry, provider callback or executable payload dispatch.
Four bounded connections permit a spawned worker's first request to wait for a
concurrent scheduler-placement commit without holding a transaction. The unit's
original autonomous deadline is never extended by host reads or evaluation.
"""

import concurrent.futures
import json
import selectors
import socket
import struct
import threading
import time

from backend.app.openevolve_broker_rpc import FixtureSchedule, dispatch_broker
from backend.app.openevolve_checkpoint_boundary import CheckpointBoundary
from backend.app.openevolve_checkpoint_publication import final_checkpoint_committed
from backend.app.openevolve_operational_router import dispatch_event
from backend.app.openevolve_socket_peer import authenticate_socket
from runtimes.openevolve_operational.transport import read_exact, unique
from runtimes.openevolve_operational.wire import (
    MAX_REPLY,
    MAX_REQUEST,
    parse_event,
    write_reply,
)

MAX_CONNECTIONS = 4
MAX_REQUESTS = 512


def _raw_frame(channel, deadline):
    size = struct.unpack("!I", read_exact(channel, 4, deadline))[0]
    if not 0 < size <= MAX_REQUEST:
        raise ValueError("Operational frame bound")
    return read_exact(channel, size, deadline)


class InitialEpochServer:
    """Listeners already created inside the host-protected launch directory."""

    def __init__(
        self,
        factory,
        fence,
        running,
        controller_binding,
        *,
        epoch_id,
        final_frontier,
        ticket_key,
        schedule,
        settings,
    ):
        if (
            type(final_frontier) is not int
            or not 1 <= final_frontier <= 2
            or type(schedule) is not FixtureSchedule
            or len(schedule.responses) != final_frontier
            or type(ticket_key) is not bytes
            or len(ticket_key) != 32
        ):
            raise ValueError("Closed initial epoch schedule required")
        if running.child is None or running.connection is None or running.finished:
            raise ValueError("Actual committed controller child required")
        self.factory, self.fence, self.running = factory, fence, running
        self.binding, self.epoch_id = controller_binding, epoch_id
        self.frontier, self.key, self.schedule, self.settings = (
            final_frontier,
            ticket_key,
            schedule,
            settings,
        )
        self._final = threading.Event()
        self._checkpoint_boundary = CheckpointBoundary()
        self._lock = threading.Lock()
        self._connections = set()
        self._used = False
        self._requests = 0
        self._acknowledgements = 0
        self._last_event = "none"
        self._started_at = time.monotonic()
        self._first_failure = None

    def _handle(self, channel, kind):
        from backend.app.openevolve_lock_admission import acquisition_window

        try:
            with acquisition_window(self.fence, self.running.deadline):
                return self._handle_in_window(channel, kind)
        finally:
            # Also close if the host-owned window itself is invalid; the inner
            # handler retains its existing tracked-connection cleanup.
            channel.close()

    def _handle_in_window(self, channel, kind):
        with self._lock:
            self._connections.add(channel)
        phase, candidate = "frame", "none"
        try:
            raw = _raw_frame(channel, self.running.deadline)
            event = parse_event(raw) if kind == "events" else None
            if event is not None:
                ticket = event.process_ticket
            else:
                envelope = json.loads(
                    raw,
                    object_pairs_hook=unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("Nonfinite broker frame")
                    ),
                )
                if type(envelope) is not dict:
                    raise ValueError("Closed broker envelope required")
                ticket = envelope.get("process_ticket")
            candidate = event.kind if event is not None else "broker"
            phase = "auth"
            peer = authenticate_socket(
                self.factory,
                self.fence,
                channel,
                admission=self.running.launch.intent.admission,
                controller_binding=self.binding,
                context=self.running.observer_context,
                epoch_id=self.epoch_id,
                process_ticket=ticket,
                ticket_key=self.key,
            )
            with self._lock:
                self._last_event = event.kind if event is not None else "broker"
            phase = "dispatch"
            with self._checkpoint_boundary.enter(
                checkpoint=event is not None and event.kind == "checkpoint_publish",
                deadline=self.running.deadline,
            ):
                if event is not None:
                    if event.epoch_id != self.epoch_id:
                        raise ValueError("Event epoch differs from host-owned unit")
                    receipt, result = dispatch_event(
                        self.factory,
                        self.fence,
                        peer,
                        event=event,
                        ticket_key=self.key,
                        settings=self.settings,
                    )
                    final = (
                        event.kind == "checkpoint_publish"
                        and result.get("published") is True
                        and type(result.get("frontier")) is int
                        and result["frontier"] == self.frontier
                        and final_checkpoint_committed(
                            self.factory,
                            self.fence,
                            peer,
                            event=event,
                            receipt_id=receipt,
                            frontier=self.frontier,
                        )
                    )
                    phase = "reply"
                    write_reply(channel, event, receipt, result, self.running.deadline)
                    with self._lock:
                        self._acknowledgements += 1
                    if final:
                        self._final.set()
                else:
                    response = dispatch_broker(
                        self.factory,
                        self.fence,
                        peer,
                        raw=raw,
                        ticket_key=self.key,
                        schedule=self.schedule,
                        settings=self.settings,
                    )
                    if (
                        not 0 < len(response) <= MAX_REPLY
                        or time.monotonic() >= self.running.deadline
                    ):
                        raise ValueError("Late or oversized committed model reply")
                    phase = "reply"
                    channel.settimeout(
                        min(65, self.running.deadline - time.monotonic())
                    )
                    channel.sendall(struct.pack("!I", len(response)) + response)
                    with self._lock:
                        self._acknowledgements += 1
        except BaseException as error:
            try:
                from backend.app.openevolve_failure_diagnostic import (
                    event_label,
                    exception_label,
                    seconds_bucket,
                )

                with self._lock:
                    if self._first_failure is None:
                        self._first_failure = {
                            "elapsed_seconds_bucket": seconds_bucket(
                                time.monotonic() - self._started_at
                            ),
                            "remaining_seconds_bucket": seconds_bucket(
                                self.running.deadline - time.monotonic()
                            ),
                            "phase": phase,
                            "untrusted_event_kind": event_label(candidate),
                            "exception": exception_label(error),
                        }
            except BaseException:  # noqa: BLE001, S110 - preserve original handler failure.
                pass
            raise
        finally:
            with self._lock:
                self._connections.discard(channel)
            channel.close()

    def progress(self):
        """Finite transport observations only, never completion/stop authority."""
        with self._lock:
            return {
                "requests": min(self._requests, MAX_REQUESTS),
                "acknowledgements": min(self._acknowledgements, MAX_REQUESTS),
                "last_event": self._last_event,
                "active_handlers": min(len(self._connections), MAX_CONNECTIONS),
            }

    def failure_diagnostic(self):
        from backend.app.openevolve_failure_diagnostic import (
            count,
            event_label,
            seconds_bucket,
        )

        now = time.monotonic()
        with self._lock:
            return {
                "elapsed_seconds_bucket": seconds_bucket(now - self._started_at),
                "remaining_seconds_bucket": seconds_bucket(self.running.deadline - now),
                "requests": count(self._requests, MAX_REQUESTS),
                "acknowledgements": count(self._acknowledgements, MAX_REQUESTS),
                "active_handlers": count(len(self._connections), MAX_CONNECTIONS),
                "authenticated_last_event": event_label(self._last_event),
                "first_handler_failure": self._first_failure,
            }

    def disconnect(self):
        """Only closes readers; never fabricates process stop, settlement or ACK."""
        self._checkpoint_boundary.close()
        with self._lock:
            for channel in tuple(self._connections):
                try:
                    channel.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                channel.close()

    def serve(self):
        if self._used:
            raise ValueError("One-shot initial epoch server")
        self._used = True
        selector = selectors.DefaultSelector()
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_CONNECTIONS, thread_name_prefix="oe-unit"
        )
        pending = set()
        try:
            for listener, kind in (
                (self.running.listener, "events"),
                (self.running.broker_listener, "broker"),
            ):
                if (
                    not isinstance(listener, socket.socket)
                    or listener.family != socket.AF_UNIX
                ):
                    raise ValueError("Fixed actual Unix listeners required")
                listener.setblocking(False)
                selector.register(listener, selectors.EVENT_READ, kind)
            pending.add(
                executor.submit(self._handle, self.running.connection, "events")
            )
            self._requests = 1
            while True:
                for future in tuple(pending):
                    if future.done():
                        pending.remove(future)
                        future.result()  # No success on hidden handler failure.
                if self._final.is_set() and not pending:
                    return {"final_frontier": self.frontier, "requests": self._requests}
                if time.monotonic() >= self.running.deadline:
                    raise ValueError(
                        "Original controller deadline reached before final ACK; "
                        + json.dumps(self.progress(), sort_keys=True)
                    )
                for key, _ in selector.select(
                    min(0.05, self.running.deadline - time.monotonic())
                ):
                    try:
                        channel, _ = key.fileobj.accept()
                    except BlockingIOError:
                        continue
                    if (
                        self._final.is_set()
                        or len(pending) >= MAX_CONNECTIONS
                        or self._requests >= MAX_REQUESTS
                    ):
                        channel.close()
                        raise ValueError(
                            "Closed operational connection/request budget exceeded"
                        )
                    self._requests += 1
                    pending.add(executor.submit(self._handle, channel, key.data))
        finally:
            selector.close()
            self.disconnect()
            # Submitted physical evaluations retain their own independent fixed
            # deadline and truthful completion handling. Never discard a running
            # host future and call the process stopped/settled from this helper.
            executor.shutdown(wait=True, cancel_futures=True)
