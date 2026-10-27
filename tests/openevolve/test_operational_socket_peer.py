"""Real socket handles with explicit65533 credential/kernel and DB doubles."""

import hashlib
import socket
import struct
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_socket_peer as resolver
from packages.research_runtime.fences import AttemptFence
from runtimes.openevolve_operational import peer as peers
from tests.openevolve.test_operational_peer import case  # noqa:F401


class CredentialSocket(socket.socket):
    def getsockopt(self, level, option, *args):
        if (level, option) == (socket.SOL_SOCKET, socket.SO_PEERCRED):
            return struct.pack("3i", 102, 65533, 65533)
        return super().getsockopt(level, option, *args)


def setup(c, monkeypatch, *, delay=False):
    context = replace(c.context, deadline=time.monotonic() + 3)
    intent = NS(id=c.worker_binding.intent_id, native_iteration=1)
    ticket_raw = resolver._ticket(b"k" * 32, c.worker_binding.epoch_id, intent.id)
    ticket = NS(
        id=uuid4(),
        epoch_id=c.worker_binding.epoch_id,
        admission_id=c.admission.id,
        intent_id=intent.id,
        ticket_sha256=hashlib.sha256(ticket_raw.encode()).hexdigest(),
    )
    worker = c.worker_binding.worker
    row = NS(
        intent_id=intent.id,
        ticket_id=ticket.id,
        **{
            k: getattr(worker, k)
            for k in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
        },
    )
    calls = []
    batches = [[], [row]] if delay else [[row]]

    class Session:
        def scalars(self, query):
            rows = batches.pop(0) if len(batches) > 1 else batches[0]
            return NS(all=lambda: rows)

        def scalar(self, query):
            return None

    deadlines = []

    @contextmanager
    def transaction(*a, **k):
        from backend.app.openevolve_lock_admission import current_window

        deadlines.append(current_window(a[1]).deadline)
        assert len(set(deadlines)) == 1  # Polling never refreshes handshake time.
        assert deadlines[-1] <= context.deadline
        calls.append("begin")
        yield Session()
        calls.append("commit")

    monkeypatch.setattr(resolver, "neutral_transaction", transaction)
    monkeypatch.setattr(
        resolver,
        "_owned_row",
        lambda s, model, identity, f: ticket if identity == ticket.id else intent,
    )
    monkeypatch.setattr(peers, "_credentials", lambda _: (102, 65533, 65533))
    monkeypatch.setattr(
        resolver,
        "require_search_worker_unit",
        lambda *a, **k: calls.append("persisted_peer"),
    )

    def sleep(_):
        assert calls[-1] == "commit"  # Never waits with run transaction held.
        calls.append("wait")

    monkeypatch.setattr(resolver.time, "sleep", sleep)
    return context, row, calls


def invoke(c, channel, context):
    return resolver.authenticate_socket(
        None,
        AttemptFence(
            c.fence.owner_id,
            c.fence.run_id,
            c.fence.attempt_id,
            c.fence.generation,
            uuid4(),
        ),
        channel,
        admission=c.admission,
        controller_binding=c.binding,
        context=context,
        epoch_id=c.worker_binding.epoch_id,
        process_ticket=c.ticket,
        ticket_key=b"k" * 32,
    )


@pytest.mark.parametrize("delay", [False, True])
def test_only_independent_persisted_exact_worker(case, monkeypatch, delay):  # noqa:F811
    context, row, calls = setup(case, monkeypatch, delay=delay)
    client, original = socket.socketpair()
    channel = CredentialSocket(fileno=original.detach())
    try:
        peer = invoke(case, channel, context)
        assert peer.binding.worker.host_pid == row.host_pid
        assert peer.binding.intent_id == row.intent_id
        assert calls[-2:] == ["persisted_peer", "commit"]
        assert ("wait" in calls) is delay
    finally:
        client.close()
        channel.close()


def test_changed_kernel_start_cannot_inherit_placement(case, monkeypatch):  # noqa:F811
    context, row, calls = setup(case, monkeypatch)
    row.start_ticks += 1
    client, original = socket.socketpair()
    channel = CredentialSocket(fileno=original.detach())
    try:
        with pytest.raises(ValueError, match="kernel worker differs"):
            invoke(case, channel, context)
        assert "persisted_peer" not in calls
    finally:
        client.close()
        channel.close()


def test_actual_current_user_socket_not_protected_child(case, monkeypatch):  # noqa:F811
    context, _, calls = setup(case, monkeypatch)
    client, host = socket.socketpair()
    try:
        with pytest.raises(ValueError, match="fixed child"):
            invoke(case, host, context)
        assert not calls
    finally:
        client.close()
        host.close()
