"""Real peer credential primitive, daemon/65533 identity doubles, no Docker proof."""

import hashlib
import os
import pickle
import socket
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.openevolve_operational import peer as module
from runtimes.openevolve_operational.daemon_identity import DaemonIdentityContext
from runtimes.rdagent_operational.peer import KernelIdentity


@pytest.fixture
def case(monkeypatch):
    ticket = "a" * 64
    worker_ticket = "b" * 64
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="openevolve_orchestrator",
        logical_operation_id=uuid4(),
        launch_ordinal=1,
        profile=module.PROFILE,
        input_sha256="c" * 64,
        reservation_id=uuid4(),
        image_id="sha256:" + "d" * 64,
        ipc=HostTicketIPC(
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
        ),
    )
    leader = KernelIdentity(100, 10, "pid:[42]", "0::/fixed", 1, (0,) * 4, (0,) * 4)
    controller = KernelIdentity(
        101, 11, "pid:[42]", "0::/fixed", 100, (65533,) * 4, (65533,) * 4
    )
    worker = KernelIdentity(
        102, 12, "pid:[42]", "0::/fixed", 101, (65533,) * 4, (65533,) * 4
    )
    binding = module.ControllerBinding(
        "e" * 64,
        hashlib.sha256(declaration_bytes(admission)).hexdigest(),
        leader,
        controller,
    )
    worker_binding = module.WorkerBinding(
        binding,
        uuid4(),
        uuid4(),
        hashlib.sha256(worker_ticket.encode()).hexdigest(),
        worker,
    )
    context = DaemonIdentityContext("e" * 64, admission.image_id, 100, 10000.0)
    calls = []
    monkeypatch.setattr(
        DaemonIdentityContext,
        "observe_pair",
        lambda self, pid: calls.append(("pair", pid)) or (leader, controller),
    )
    monkeypatch.setattr(
        DaemonIdentityContext,
        "observe",
        lambda self, pid: calls.append(("worker", pid)) or worker,
    )
    monkeypatch.setattr(module, "_credentials", lambda channel: (101, 65533, 65533))
    fence = SimpleNamespace(
        **{
            k: getattr(admission, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    return SimpleNamespace(
        admission=admission,
        binding=binding,
        worker_binding=worker_binding,
        context=context,
        ticket=ticket,
        worker_ticket=worker_ticket,
        calls=calls,
        fence=fence,
    )


def auth(c, **kw):
    args = {"binding": c.binding, "context": c.context, "process_ticket": c.ticket}
    args.update(kw)
    return module.authenticate(object(), c.admission, **args)


def test_controller_reobserved_each_event(case):
    peer = auth(case)
    module.require_peer(peer, case.fence, admission_id=case.admission.id)
    module.require_peer(peer, case.fence, admission_id=case.admission.id)
    assert case.calls == [("pair", 101)] * 3
    with pytest.raises(TypeError):
        pickle.dumps(peer)


def test_worker_exact_assignment_not_descendant_membership(case, monkeypatch):
    monkeypatch.setattr(module, "_credentials", lambda channel: (102, 65533, 65533))
    peer = auth(case, binding=case.worker_binding, worker_ticket=case.worker_ticket)
    module.require_peer(
        peer,
        case.fence,
        admission_id=case.admission.id,
        epoch_id=case.worker_binding.epoch_id,
        intent_id=case.worker_binding.intent_id,
    )
    assert case.calls == [("pair", 101), ("worker", 102), ("pair", 101)] * 2
    with pytest.raises(ProcessError):
        module.require_peer(
            peer,
            case.fence,
            admission_id=case.admission.id,
            epoch_id=case.worker_binding.epoch_id,
            intent_id=uuid4(),
        )


@pytest.mark.parametrize(
    "pid,uid,gid",
    [(102, 65533, 65533), (103, 65533, 65533), (101, 0, 0), (101, 65533, 0)],
)
def test_wrong_peer_cannot_claim_controller(case, monkeypatch, pid, uid, gid):
    monkeypatch.setattr(module, "_credentials", lambda channel: (pid, uid, gid))
    with pytest.raises(ProcessError):
        auth(case)


@pytest.mark.parametrize(
    "key,value",
    [
        ("process_ticket", "f" * 64),
        ("process_ticket", None),
        ("worker_ticket", "b" * 64),
        ("context", object()),
    ],
)
def test_no_ticket_or_context_authority_shortcut(case, key, value):
    with pytest.raises(ProcessError):
        auth(case, **{key: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "f" * 64),
        ("image_id", "sha256:" + "f" * 64),
        ("leader_pid", 999),
    ],
)
def test_exact_daemon_context(case, field, value):
    with pytest.raises(ProcessError):
        auth(case, context=replace(case.context, **{field: value}))


def test_reused_worker_pid_fails_fresh_check(case, monkeypatch):
    monkeypatch.setattr(module, "_credentials", lambda channel: (102, 65533, 65533))
    peer = auth(case, binding=case.worker_binding, worker_ticket=case.worker_ticket)
    monkeypatch.setattr(
        DaemonIdentityContext,
        "observe",
        lambda self, pid: replace(case.worker_binding.worker, start_ticks=99),
    )
    with pytest.raises(ProcessError):
        module.require_peer(
            peer,
            case.fence,
            admission_id=case.admission.id,
            epoch_id=case.worker_binding.epoch_id,
            intent_id=case.worker_binding.intent_id,
        )


def test_controller_drift_after_worker_observation(case, monkeypatch):
    monkeypatch.setattr(module, "_credentials", lambda channel: (102, 65533, 65533))
    pairs = iter(
        (
            (case.binding.leader, case.binding.controller),
            (case.binding.leader, replace(case.binding.controller, start_ticks=999)),
        )
    )
    monkeypatch.setattr(
        DaemonIdentityContext, "observe_pair", lambda self, pid: next(pairs)
    )
    with pytest.raises(ProcessError):
        auth(case, binding=case.worker_binding, worker_ticket=case.worker_ticket)


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_fresh_fence_exact(case, field):
    peer = auth(case)
    setattr(case.fence, field, 2 if field == "generation" else uuid4())
    with pytest.raises(ProcessError):
        module.require_peer(peer, case.fence, admission_id=case.admission.id)


def test_real_unix_socketpair_credentials():
    left, right = socket.socketpair()
    try:
        assert module._credentials(left) == (os.getpid(), os.getuid(), os.getgid())
    finally:
        left.close()
        right.close()
