"""Structured peer consumes fresh exact typed triples, with real socket objects."""

import hashlib
import socket
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.openevolve_operational.daemon_identity import (
    DaemonIdentityContext,
    DaemonMemberObservation,
)
from runtimes.openevolve_structured_host import peer as module
from runtimes.rdagent_operational.peer import KernelIdentity


@pytest.fixture
def case(monkeypatch):
    ticket, worker_ticket = "a" * 64, "b" * 64
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
    binding = module.WorkerBinding(
        binding,
        uuid4(),
        uuid4(),
        hashlib.sha256(worker_ticket.encode()).hexdigest(),
        worker,
    )
    observed = DaemonMemberObservation(leader, controller, worker)
    context = DaemonIdentityContext("e" * 64, admission.image_id, 100, 60.0)
    members = Mock(return_value=observed)
    pair = Mock(return_value=(leader, controller))
    monkeypatch.setattr(
        DaemonIdentityContext, "observe_members", lambda self, *a: members(*a)
    )
    monkeypatch.setattr(
        DaemonIdentityContext, "observe_pair", lambda self, *a: pair(*a)
    )
    monkeypatch.setattr(
        DaemonIdentityContext,
        "observe",
        Mock(side_effect=AssertionError("unbatched worker observation")),
    )
    monkeypatch.setattr(module, "_credentials", lambda c: (102, 65533, 65533))
    fence = NS(
        **{
            k: getattr(admission, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    left, right = socket.socketpair()

    def authenticate(current=binding):
        return module.authenticate(
            left,
            admission,
            binding=current,
            context=context,
            process_ticket=ticket,
            worker_ticket=worker_ticket
            if type(current) is module.WorkerBinding
            else None,
        )

    def require(peer):
        return module.require_peer(
            peer,
            fence,
            admission_id=admission.id,
            epoch_id=binding.epoch_id,
            intent_id=binding.intent_id,
        )

    yield NS(**locals())
    left.close()
    right.close()


def test_worker_requires_new_observation_on_every_auth_and_recheck(case):
    peer = case.authenticate()
    assert case.require(peer) is peer
    assert case.members.call_count == 2
    assert all(c.args == (101, 102) for c in case.members.call_args_list)
    case.pair.assert_not_called()


def test_controller_only_path_keeps_existing_pair_api(case, monkeypatch):
    monkeypatch.setattr(module, "_credentials", lambda c: (101, 65533, 65533))
    case.authenticate(case.binding.controller)
    case.pair.assert_called_once_with(101)
    case.members.assert_not_called()


@pytest.mark.parametrize("member", ["leader", "controller", "worker"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("start_ticks", 999),
        ("pid_namespace", "pid:[43]"),
        ("cgroup", "0::/other"),
        ("ppid", 999),
        ("uids", (1,) * 4),
        ("gids", (1,) * 4),
    ],
)
def test_persisted_identity_mismatch_rejected_after_prior_success(
    case, member, field, value
):
    peer = case.authenticate()
    different = replace(getattr(case.observed, member), **{field: value})
    case.members.return_value = replace(case.observed, **{member: different})
    with pytest.raises(ProcessError):
        case.require(peer)


@pytest.mark.parametrize(
    "bad", [None, (), (None, None, None), NS(leader=None, controller=None, worker=None)]
)
def test_missing_or_untyped_batch_has_no_fallback(case, bad):
    case.members.return_value = bad
    with pytest.raises(ProcessError):
        case.authenticate()
    case.pair.assert_not_called()


@pytest.mark.parametrize(
    "credentials", [(999, 65533, 65533), (102, 0, 65533), (102, 65533, 0)]
)
def test_socket_credentials_still_exact_after_batch(case, monkeypatch, credentials):
    monkeypatch.setattr(module, "_credentials", lambda c: credentials)
    with pytest.raises(ProcessError):
        case.authenticate()
    case.members.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "f" * 64),
        ("image_id", "sha256:" + "f" * 64),
        ("leader_pid", 999),
    ],
)
def test_context_mismatch_rejected_before_observation(case, field, value):
    peer = case.authenticate()
    object.__setattr__(peer, "_context", replace(case.context, **{field: value}))
    with pytest.raises(ProcessError):
        case.require(peer)
    assert case.members.call_count == 1


def test_failed_daemon_observation_propagates_without_fallback(case):
    case.members.side_effect = ProcessError("oe_controller_daemon_identity")
    with pytest.raises(ProcessError):
        case.authenticate()
    case.pair.assert_not_called()


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_outer_fence_check_still_required_after_observation(case, field):
    peer = case.authenticate()
    setattr(case.fence, field, 2 if field == "generation" else uuid4())
    with pytest.raises(ProcessError):
        case.require(peer)
    assert case.members.call_count == 2


def test_duck_typed_equality_is_not_an_observation(case):
    class Pretender:
        def __eq__(self, other):
            return True

    case.members.return_value = Pretender()
    with pytest.raises(ProcessError):
        case.authenticate()
