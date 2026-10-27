"""Kernel observations plus explicit authority doubles, not Docker acceptance."""

import hashlib
import os
import socket
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    NoIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.rdagent_operational import peer as module

TICKET = "a" * 64


def declaration(**changes):
    values = {
        "schema_version": "mineagent.process-admission.v2",
        "id": uuid4(),
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
        "role": "rd_orchestrator",
        "logical_operation_id": uuid4(),
        "launch_ordinal": 1,
        "profile": module.PROFILE,
        "input_sha256": "b" * 64,
        "reservation_id": uuid4(),
        "image_id": "sha256:" + "c" * 64,
        "ipc": HostTicketIPC(
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(TICKET.encode()).hexdigest(),
        ),
    }
    values.update(changes)
    return ProcessDeclarationV2(**values)


def binding(admission):
    leader = module.KernelIdentity(
        100, 1000, "pid:[12]", "0::/exact\n", 1, (0,) * 4, (0,) * 4
    )
    child = module.KernelIdentity(
        101, 1001, "pid:[12]", "0::/exact\n", 100, (65533,) * 4, (65533,) * 4
    )
    return module.TrustedControllerBinding(
        hashlib.sha256(declaration_bytes(admission)).hexdigest(),
        leader,
        child,
    )


@pytest.fixture
def authority(monkeypatch):
    admission = declaration()
    trusted = binding(admission)
    observations = {100: trusted.leader, 101: trusted.controller}
    credentials = [101, 65533, 65533]
    monkeypatch.setattr(module, "observe_identity", lambda pid: observations[pid])
    monkeypatch.setattr(module, "_credentials", lambda connection: tuple(credentials))
    peer = module.authenticate_controller(
        object(), admission, binding=trusted, supplied_ticket=TICKET
    )
    fence = SimpleNamespace(
        **{
            key: getattr(admission, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    return peer, fence, observations, credentials


def test_real_socket_peercred_and_host_identity():
    left, right = socket.socketpair()
    try:
        assert module._credentials(left) == (os.getpid(), os.getuid(), os.getgid())
        identity = module.observe_identity(os.getpid())
        assert identity.host_pid == os.getpid()
        assert identity.ppid == os.getppid()
        assert identity.uids == (*os.getresuid(), os.geteuid())
        assert identity.gids == (*os.getresgid(), os.getegid())
        assert identity.start_ticks > 0
        assert module.observe_identity(os.getpid()) == identity
    finally:
        left.close()
        right.close()


def test_real_child_identity_and_exit():
    # Local sandbox rejects AF_UNIX listener creation. This checks real process
    # lifecycle only, not a protected-profile socket authentication positive.
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
    )
    try:
        identity = module.observe_identity(child.pid)
        assert identity.ppid == os.getpid()
        assert identity.host_pid != os.getpid()
        child.communicate(b"x", timeout=5)
        with pytest.raises(ProcessError):
            module.observe_identity(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_real_closed_and_non_unix_socket_rejected():
    with socket.socket(socket.AF_INET) as connection, pytest.raises(ProcessError):
        module._credentials(connection)
    left, right = socket.socketpair()
    left.close()
    right.close()
    with pytest.raises(OSError):
        module._credentials(left)


def test_valid_authority_double_revalidates_each_event(authority):
    peer, fence, _, _ = authority
    assert (
        module.require_controller_peer(
            peer, fence, logical_operation_id=peer.admission.logical_operation_id
        )
        is peer
    )
    with pytest.raises(TypeError, match="cannot be serialized"):
        peer.__reduce__()


@pytest.mark.parametrize("which", ["leader", "controller"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("start_ticks", 9999),
        ("pid_namespace", "pid:[99]"),
        ("cgroup", "0::/other\n"),
        ("uids", (1,) * 4),
        ("gids", (1,) * 4),
        ("ppid", 999),
    ],
)
def test_every_event_rejects_changed_observed_identity(authority, which, field, value):
    peer, fence, observations, _ = authority
    old = getattr(peer.binding, which)
    observations[old.host_pid] = replace(old, **{field: value})
    with pytest.raises(ProcessError):
        module.require_controller_peer(
            peer, fence, logical_operation_id=peer.admission.logical_operation_id
        )


@pytest.mark.parametrize(
    "credentials", [(102, 65533, 65533), (101, 0, 65533), (101, 65533, 0)]
)
def test_event_rejects_substituted_socket_or_uid(authority, credentials):
    peer, fence, _, current = authority
    current[:] = credentials
    with pytest.raises(ProcessError):
        module.require_controller_peer(
            peer, fence, logical_operation_id=peer.admission.logical_operation_id
        )


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_exact_fence_required(authority, field):
    peer, fence, _, _ = authority
    setattr(fence, field, 2 if field == "generation" else uuid4())
    with pytest.raises(ProcessError):
        module.require_controller_peer(
            peer, fence, logical_operation_id=peer.admission.logical_operation_id
        )


def test_wrong_logical_operation_and_forged_authority(authority):
    peer, fence, _, _ = authority
    with pytest.raises(ProcessError):
        module.require_controller_peer(peer, fence, logical_operation_id=uuid4())
    with pytest.raises(ProcessError):
        module.require_controller_peer(
            replace(peer, _issuer=object()),
            fence,
            logical_operation_id=peer.admission.logical_operation_id,
        )


@pytest.mark.parametrize("ticket", ["b" * 64, "A" * 64, "a" * 63, None, 12])
def test_ticket_must_be_exact_secret(authority, ticket):
    peer, _, _, _ = authority
    with pytest.raises(ProcessError):
        module.authenticate_controller(
            object(), peer.admission, binding=peer.binding, supplied_ticket=ticket
        )


def test_admission_digest_and_profile_and_noipc_rejected(authority):
    peer, _, _, _ = authority
    changed = peer.admission.model_copy(update={"input_sha256": "d" * 64})
    with pytest.raises(ProcessError):
        module.authenticate_controller(
            object(), changed, binding=peer.binding, supplied_ticket=TICKET
        )
    old = declaration(profile="mineagent.rdagent-orchestrator.v1")
    candidate = declaration(
        role="factor_cutoff_candidate", ipc=NoIPC(ipc_policy="none", ticket_sha256=None)
    )
    for value in (old, candidate):
        with pytest.raises(ProcessError):
            module.authenticate_controller(
                object(), value, binding=binding(value), supplied_ticket=TICKET
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("host_pid", 100),
        ("ppid", 99),
        ("pid_namespace", "pid:[99]"),
        ("cgroup", "0::/other\n"),
        ("uids", (0,) * 4),
        ("gids", (0,) * 4),
    ],
)
def test_binding_cannot_choose_sibling_descendant_or_privileged_child(field, value):
    trusted = binding(declaration())
    with pytest.raises(ProcessError):
        replace(trusted, controller=replace(trusted.controller, **{field: value}))


def test_kernel_identity_rejects_bool_pid():
    with pytest.raises(ProcessError):
        module.observe_identity(True)


def test_boolean_fence_generation_rejected(authority):
    peer, fence, _, _ = authority
    fence.generation = True
    with pytest.raises(ProcessError):
        module.require_controller_peer(
            peer, fence, logical_operation_id=peer.admission.logical_operation_id
        )


def test_observation_rejects_credential_change_between_reads(monkeypatch):
    original = module._status_identity
    calls = []

    def changed(pid, identity):
        value = original(pid, identity)
        calls.append(value)
        return value if len(calls) == 1 else replace(value, uids=(999,) * 4)

    monkeypatch.setattr(module, "_status_identity", changed)
    with pytest.raises(ProcessError):
        module.observe_identity(os.getpid())
    assert len(calls) == 2
